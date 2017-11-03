#! /usr/bin/env python

import rospy
import roslib
from geometry_msgs.msg import PointStamped
import cv2
from cv_bridge import CvBridge, CvBridgeError
from sensor_msgs.msg import Image
from sensor_msgs.msg import CameraInfo
import numpy as np
import pprint
from image_geometry import PinholeCameraModel
pp = pprint.PrettyPrinter(indent = 4)
from sensor_msgs.msg import PointCloud2
import sensor_msgs.point_cloud2 as pc2
bridge = CvBridge()

#Set to true to get debug info

hue_threshold = 80

#Thresholds in hue for all collors
colors = ["green","red","blue_low","blue_high","yellow"]


#map of (lower,upper) thresholds in hsv for the respective colors
#HSV values in open cv are given in ranges (0-180,0-255,0-255) respectively
color_2_rgb = {
    "green" : (0,255,0),
    "red" : (255,0,0),
    "blue_low" : (0,0,255),
    "blue" : (0,0,255),
    "yellow" : (0,255,255),
}

# Takes a standard hsv point and transforms it into the form used by opencv.
def hsv_2_opencv(hsv):
    h = hsv[0]; s=hsv[1]; v=hsv[2]
#    h = max(h,0); h = min(h,360) #in range [0,360]
#    s = max(s,0); s = min(s,100)
#    v = max(v,0); v = min(v,100)
    return np.array([int(h//2),int(s*2.55),int(v*2.55)],dtype=np.uint8)
color_space = np.zeros((255,180,3),dtype=np.uint8)
for x in range(255):
    for y in range(180):
        color_space[x,y,1] = x
        color_space[x,y,0] = y
        color_space[x,y,2] = 255
cv_color_space =  cv2.cvtColor(color_space, cv2.COLOR_HSV2BGR)
cv2.imwrite('very_secret_open_cv_hsv_space.png',cv_color_space)



#Detects objects from the camera
class ObjectDetector:

    def __init__(self):
        
        #These values are set when we get new data and unset when we have used the data
        self._have_received_image = False
        self._have_received_depth = False
        self._has_received_cam_info = False
 
        self.obj_cand_pub = rospy.Publisher("/camera/object_candidates",PointStamped,queue_size=10)
        
        self.image_sub = rospy.Subscriber("/camera/rgb/image_rect_color",Image,self.image_callback)
        
        #self.depth_sub = rospy.Subscriber("/camera/depth_registered/points",PointCloud2,self.depth_callback)
        self.depth_sub = rospy.Subscriber("/camera/depth_registered/sw_registered/image_rect",Image,self.depth_callback)
        # This should probably be sub depth_registered/points. If we don't have it published,
        # check http://wiki.ros.org/rgbd_launch
        # and http://wiki.ros.org/realsense_camera#ROS_API
        # Registration => Matching two point sets
        # Rectification => Projecting two images onto a common plane, in our case to ease Registration
        # It is probably correct to use image_rect_color aswell.
        
        self.info_sub = rospy.Subscriber("/camera/rgb/camera_info",CameraInfo,self.info_callback)
        self.load_hsv_thresholds()
        self.camera_model = PinholeCameraModel()          

        if DEBUGGING: 
            self.dbg_img_pub = rospy.Publisher("/camera/debug/img",Image,queue_size=1)
            self.h_img_pub = rospy.Publisher("/camera/debug/hsv/h",Image,queue_size=1)
            self.h_mask_pub = rospy.Publisher("/camera/debug/h/mask",Image,queue_size=1)
            self.hsv_scale_pub = rospy.Publisher("/camera/debug/hsv/scale/",Image,queue_size=1)


    def load_hsv_thresholds(self):
        self.set_hsv_thresholds()
        #thresholds = rospy.get_param("/camera/hsv_thresholds")
        #self.hsv_thresholds = dict()
        #for color,thresh in thresholds.iteritems():
        #    assert(color in colors)
        #    lower = hsv_2_opencv(thresholds[color]["lower"])
        #    upper = hsv_2_opencv(thresholds[color]["upper"])
        #    self.hsv_thresholds[color] = (lower,upper)
   
    def set_hsv_thresholds(self):
        self.hsv_thresholds = {
            "red":(np.array([110,240,10]),np.array([120,255,255])),
            "green":(np.array([45,50,10]),np.array([85,255,255])),
            "yellow":(np.array([0,180,100]),np.array([0,255,255])),
            "blue":(np.array([18,100,15]),np.array([35,256,200])),
            "blue_high":(np.array([150,0,0]),np.array([180,255,255]))}

    def image_callback(self,ros_image):
        try:
            self.rgb_image_msg = ros_image
            self._have_received_image = True
        except CvBridgeError as e:
            rospy.logdebug(e)
        #lab_image = cv2.cvtColor(rgb_image, cv2.COLOR_BGR2LAB)
        #YCrCb_image = cv2.cvtColor(rgb_image, cv2.COLOR_BGR2YCrCb)
    
    # Get the depth image
    def depth_callback(self,depth_msg):
        #Only transforming the images we use might save computations depending on implementation of the bridge
        self.depth_msg = depth_msg 
        #bridge.imgmsg_to_cv2(ros_depth)
        self._have_received_depth = True 
    # Get the projection matrix
    def info_callback(self,info_message):
        self.camera_model.fromCameraInfo(info_message)
        self._has_received_cam_info = True
    
    _mask_kernel = np.ones((3,3),np.uint8)
    def compute_mask(self,image,(lower,upper)):

        mask = cv2.inRange(image,lower,upper)

        cv2.morphologyEx(mask,cv2.MORPH_OPEN,
            kernel = self._mask_kernel,
            dst=mask,
            iterations = 10)

        return mask

    #Process image :D
    def image_processing(self):
        
        if DEBUGGING: 
            self.hsv_scale_pub.publish(bridge.cv2_to_imgmsg(cv_color_space,"rgb8"))
        #if self._have_received_image and self._have_received_depth and self._has_received_cam_info: 
        if self._have_received_image and  self._has_received_cam_info and self._have_received_depth:
            rgb_image = bridge.imgmsg_to_cv2(self.rgb_image_msg,"rgb8")
            depth_image = bridge.imgmsg_to_cv2(self.depth_msg,"passthrough")
            hsv_image =  cv2.cvtColor(rgb_image, cv2.COLOR_BGR2HSV)
            #cv2.GaussianBlur(hsv_image,(11,11),3,hsv_image)
            if DEBUGGING:
                mask_union = None
                rgb_dbg = rgb_image.copy()
                self.load_hsv_thresholds()
            
            for color in ["blue"]: 
                mask = self.compute_mask(hsv_image,self.hsv_thresholds[color]) 
                
                if DEBUGGING:
                    if mask_union is None:
                        mask_union = np.zeros_like(mask)
                    cv2.bitwise_or(mask_union,mask,mask_union)

                contours,_ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                
                if DEBUGGING:
                    h_image = hsv_image[:,:,0].copy()
                    self.h_img_pub.publish(bridge.cv2_to_imgmsg(h_image,"mono8"))
                
                
                for contour in contours:
                    
                    bot_right = (int(contour[:,0,0].max()),int(contour[:,0,1].max()))
                    top_left = (int(contour[:,0,0].min()),int(contour[:,0,1].min()))
                    middle = (int(contour[:,0,0].mean()),int(contour[:,0,1].mean()))
                    #pc = pc2.read_points(self.depth_msg,skip_nans=False,field_names=None,uvs=[middle])
                    #point = pc.next()
                    #print point
                    z = np.nanmean(depth_image[middle[1]-10:middle[1]+10,middle[0]-10:middle[0]+10])
                    point = np.array(self.camera_model.projectPixelTo3dRay(middle))
                    print "distance from camera =", z
                    point = point * z
                    obj_cand_msg = PointStamped()
                    obj_cand_msg.header.stamp = rospy.Time.now()
                    obj_cand_msg.header.frame_id = "/camera_link" #We might need to change this to it's propper value
                    obj_cand_msg.point.x = point[2]
                    obj_cand_msg.point.y = - point[0]
                    obj_cand_msg.point.z = - point[1]
                    
                    print "x from camera coord=", point[2]
                    print "y from camera coord=", - point[0]
                    print "z from camera coord=", - point[1]
                    self.obj_cand_pub.publish(obj_cand_msg)
                     
                    if DEBUGGING:
                        cv2.drawContours(rgb_dbg,[contour],-1,color=color_2_rgb[color],thickness=-1)
                        cv2.circle(rgb_dbg,middle,radius=5,color=(0,0,0),thickness=2)
                        cv2.rectangle(rgb_dbg,top_left,bot_right,color=(0,0,0),thickness=2)
                         
            if DEBUGGING:
                    
                ros_out_image = bridge.cv2_to_imgmsg(rgb_dbg,"rgb8")
                self.dbg_img_pub.publish(ros_out_image)
                self.h_mask_pub.publish(
                    bridge.cv2_to_imgmsg(mask_union,"mono8"))

        self._have_received_image = False
        self._have_received_depth = False
        

    #Detects objects untill shutdown. Permanently blocking.
    def detect_forever(self,rate=10):
        rate = rospy.Rate(rate)
        rate.sleep()
        while not rospy.is_shutdown():
            self.image_processing()
            rate.sleep()         

def main():
    #Main fucntion. Put everything here
    global DEBUGGING
    DEBUGGING = rospy.get_param("/camera/debugging",True)
    
    rospy.init_node("object_candidate_spotter")
    
    spotter = ObjectDetector()
    spotter.detect_forever()

if __name__ == "__main__":
    try:
        main()
    except rospy.ROSInterruptException:
        pass
