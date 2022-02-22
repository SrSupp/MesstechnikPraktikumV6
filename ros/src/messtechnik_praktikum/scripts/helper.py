#!/usr/bin/env python3

from shutil import move
import sys
import copy
from time import sleep
from turtle import position
import rospy
import moveit_commander
import moveit_msgs.msg
import geometry_msgs.msg
import shape_msgs.msg
import rospkg
import os
from math import pi
from std_msgs.msg import String
from moveit_commander.conversions import pose_to_list
from tf.transformations import *
# Import with ROS reference for runtime and relative path reference for IDE
from applications_helene.helpy import *
try:
  from applications_helene.scripts.helpy import *
except Exception:
  pass
## END_SUB_TUTORIAL

class MoveGroupHelper(helpy):
  """MoveGroupHelper, inherits from helpy"""
  def __init__(self):
    """Constructor for MoveGroupHelper"""
    super().__init__()

    # Get planning scene interface
    self.planning_scene = moveit_commander.PlanningSceneInterface()

    # Get planning frame and end effector link
    self.planning_frame = self.move_group.get_planning_frame()
    self.eef_link = self.move_group.get_end_effector_link()

    # Define cylinder containing tumor
    self.cylinder_pose = geometry_msgs.msg.PoseStamped()
    self.cylinder_pose.header.frame_id = "world"
    self.cylinder_pose.pose.position.x = 0.3
    self.cylinder_pose.pose.position.z = 0.035
    self.cylinder_pose.pose.orientation.w = 1.0
    self.cylinder_height = 0.08
    self.cylinder_radius = 0.04
    self.cylinder_name = 'full_cylinder'
    self.hollow_cylinder_name = 'hollow_cylinder'

    # Get ROS package utility for access to package locations
    self.rospack = rospkg.RosPack()

    # Sleep for 5 seconds to allow planning scene interface to properly wake up
    sleep(5)

    # Save current robot pose as start pose
    self.start_pose = copy.deepcopy(self.move_group.get_current_pose().pose)

    # Add cylinder containing tumor to scene
    self.__add_cylinder()


  def get_cylinder_pose(self):
    """Get pose message for cylinder from planning scene"""
    if self.cylinder_name in self.planning_scene.get_known_object_names():
      self.cylinder_pose.pose = self.planning_scene.get_object_poses([self.cylinder_name])[self.cylinder_name]


  def __add_cylinder(self, timeout=10):
    """Add cylinder to planning scene"""
    # Add cylinder and wait for planning scene to update
    self.planning_scene.add_cylinder(self.cylinder_name, self.cylinder_pose, self.cylinder_height, self.cylinder_radius)
    return self.__wait_for_state_update(self.cylinder_name, cylinder_is_known=True, timeout=timeout)


  def __get_relative_orientation(self, target_link_name, reference_link_name):
    """Calculate target link orientation relative to refernce link orientation"""
    # Compute inverse quaternion of reference link orientation
    reference_orientation = self.move_group.get_current_pose(target_link_name).pose.orientation
    reference_orientation_inverted = [reference_orientation.x, reference_orientation.y, reference_orientation.z, -reference_orientation.w]

    # Compute quaternion of target link orientation
    target_orientation_msg = self.move_group.get_current_pose(reference_link_name).pose.orientation
    target_orientation = [target_orientation_msg.x, target_orientation_msg.y, target_orientation_msg.z, target_orientation_msg.w]

    # Multiply the two quaternions to get the target links orientation relative to the reference links orientation
    relative_orientation = quaternion_multiply(reference_orientation_inverted, target_orientation)

    # Create orientation message
    relative_orientation_msg = geometry_msgs.msg.Quaternion(relative_orientation[0], relative_orientation[1], relative_orientation[2], relative_orientation[3])

    return relative_orientation_msg


  def __get_orientation_constraints(self):
    """Create orientation constraints that lock robots current end effector orientation"""
    # Create empty orientation constraint message
    orientation_constraint = moveit_msgs.msg.OrientationConstraint()

    # Set constrained link and base link for coordinate reference
    orientation_constraint.link_name = "axis_6"
    orientation_constraint.header.frame_id = "axis_0"

    # Get orientation of constrained link relative to reference link
    relative_orientation_msg = self.__get_relative_orientation("axis_6", "axis_0")

    # Set orientation constraint
    orientation_constraint.orientation = relative_orientation_msg

    # Set tolerances around each axis
    orientation_constraint.absolute_x_axis_tolerance = 0.1
    orientation_constraint.absolute_y_axis_tolerance = 0.1
    orientation_constraint.absolute_z_axis_tolerance = 0.1
    orientation_constraint.weight = 1.0

    return orientation_constraint


  def __get_position_constraints(self):
    """Create position constraints (currently not working)"""
    # TODO Get position constraints working
    # Create empty constraint message
    position_constraint = moveit_msgs.msg.PositionConstraint()

    # Set constrained link and base link for coordinate reference
    position_constraint.link_name = "axis_6"
    position_constraint.header.frame_id = "axis_0"

    # Define constraint region in the shape of a narrow cylinder
    position_constraint.target_point_offset.z = 0.5
    bounding_region = shape_msgs.msg.SolidPrimitive()
    bounding_region.type = bounding_region.CYLINDER
    bounding_region.dimensions = [0, 0]
    bounding_region.dimensions[bounding_region.CYLINDER_HEIGHT] = 1
    bounding_region.dimensions[bounding_region.CYLINDER_RADIUS] = 0.01

    # Add constraint region to position constraint
    position_constraint.constraint_region.primitives.append(bounding_region)

    return position_constraint


  def __apply_constraints(self):
    """Apply motion constraints (orientation and position)"""
    # Create empty constraints message
    constraints = moveit_msgs.msg.Constraints()

    # Create orientation constraint and position constraint
    orientation_constraint = self.__get_orientation_constraints()
    position_constraint = self.__get_position_constraints()

    # Add constraints to constraints message
    constraints.orientation_constraints.append(orientation_constraint)
    constraints.position_constraints.append(position_constraint)

    # Apply constraints
    self.move_group.set_path_constraints(constraints)
    self.cartesian_constraints = constraints


  def __clear_constraints(self):
    """Clear motion constraints"""
    self.cartesian_constraints = None
    self.move_group.clear_path_constraints()


  def __remove_cylinder(self, timeout=4):
    """Remove cylinder from planning scene"""
    self.__wait_for_state_update(self.cylinder_name, cylinder_is_known=True, timeout=timeout)

    # Remove cylinder from the scene
    self.planning_scene.remove_attached_object(name=self.cylinder_name)
    self.planning_scene.remove_world_object(name=self.cylinder_name)

    # We wait for the planning scene to update.
    return self.__wait_for_state_update(self.cylinder_name, cylinder_is_known=False, timeout=timeout)


  def enable_probing(self):
    """Enable probing, which allows the robot to pierce the cylinder from the top and imposes motion constraints"""
    # Apply orientation constraints
    self.__apply_constraints()
    # Replace solid cylinder with hollow cylinder
    self.__add_hollow_cylinder()
    self.__remove_cylinder()
    # Set slower movement speed
    self.move_group.set_max_velocity_scaling_factor(0.01)
    self.move_group.set_max_acceleration_scaling_factor(0.1)


  def disable_probing(self):
    """Disable probing"""
    # Replace hollow cylinder with solid cylinder
    self.__add_cylinder()
    self.__remove_hollow_cylinder()
    # Clear orientation constraints
    self.__clear_constraints()
    # Reset movement speed to normal speed
    self.move_group.set_max_velocity_scaling_factor(0.8)
    self.move_group.set_max_acceleration_scaling_factor(1)


  def go_to_probing_pos(self):
    """Go to probing position"""
    # Create copy of current pose
    wpose = copy.deepcopy(self.move_group.get_current_pose().pose)
    
    # Set first waypoint
    wpose.position.z -= 0.27  # First move down (z)
    wpose.position.x += 0.27  # Second move forward in (x)

    # Set orientation
    quat = quaternion_from_euler(0, -pi/2, pi)
    wpose.orientation.x = quat[0]
    wpose.orientation.y = quat[1]
    wpose.orientation.z = quat[2]
    wpose.orientation.w = quat[3]

    # Go to position
    self.move_group.plan()
    self.move_group.go(copy.deepcopy(wpose))


  def go_home(self):
    """Go to home position (pose saved at startup)"""
    self.move_group.plan()
    self.move_group.go(copy.deepcopy(self.start_pose))


  def plan_probing(self, z = -0.1):
    """Plan cartesian motion for probing, where z defines the vertical motion (default = 0.1m)"""
    # Create waypoints
    waypoints = []

    # Create copy of current pose
    wpose = copy.deepcopy(self.move_group.get_current_pose().pose)
    
    # Set first waypoint
    wpose.position.z += z   # Move down (z)

    # Append target pose to waypoints
    waypoints.append(copy.deepcopy(wpose))

    self.move_group.plan()
    (plan, fraction) = self.move_group.compute_cartesian_path(
                        waypoints,                                        # waypoints to follow
                        0.01,                                             # eef_step
                        0.0,                                              # jump_threshold
                        path_constraints = self.cartesian_constraints)    # constraints

    # Note: We are just planning, not asking move_group to actually move the robot yet:
    return plan, fraction


  def __wait_for_state_update(self, cylinder_name, cylinder_is_known=False, timeout=4):
    """Waits for planning scene to update or times out after given timeout (default=4s)"""
    start = rospy.get_time()
    seconds = rospy.get_time()
    while (seconds - start < timeout) and not rospy.is_shutdown():
      
      # Test if the cylinder is in the scene.
      # Note that attaching the cylinder will remove it from known_objects
      is_known = cylinder_name in self.scene.get_known_object_names()

      # Test if we are in the expected state
      if cylinder_is_known == is_known:
        return True

      # Sleep so that we give other threads time on the processor
      rospy.sleep(0.1)
      seconds = rospy.get_time()

    # If we exited the while loop without returning then we timed out
    return False


  def execute_plan(self, plan):
    """Execute given plan"""
    self.move_group.execute(plan, wait=True)


  def __add_hollow_cylinder(self):
    """Add hollow cylinder to planning scene"""
    cylinder_pose = copy.deepcopy(self.cylinder_pose)
    cylinder_pose.pose.position.z -= 0.035
    filename = os.path.join(self.rospack.get_path('messtechnik_praktikum') ,"stl/hollow_cylinder.stl")
    self.planning_scene.add_mesh(self.hollow_cylinder_name, pose=cylinder_pose, filename=filename)
    self.__wait_for_state_update(self.hollow_cylinder_name, cylinder_is_known=True)


  def __remove_hollow_cylinder(self):
    """"Remove the hollow cylinder from the planning scene"""
    # Remove cylinder from the scene
    self.planning_scene.remove_world_object(self.hollow_cylinder_name)
    self.__wait_for_state_update(self.hollow_cylinder_name, cylinder_is_known=False)

    # We wait for the planning scene to update.
    return self.__wait_for_state_update(self.cylinder_name, cylinder_is_known=False, timeout=4)

def main():
  try:
    helper = MoveGroupHelper()

    input("\n-- Press ENTER to start the demo --")

    helper.go_to_probing_pos()

    input("\n-- Press ENTER to turn on the blue light --")

    helper.set_led_blue(255)

    input("\n-- Press ENTER to turn on the green light --")

    helper.set_led_green(255)

    input("\n-- Press ENTER to turn off the lights --")

    helper.set_led_blue(0)
    helper.set_led_green(0)

    input("\n-- Press ENTER to start probing --")

    helper.enable_probing()

    cartesian_plan, fraction = helper.plan_probing(z=-0.06)

    helper.execute_plan(cartesian_plan)

    input("\n-- Press ENTER to retract the needle --")

    cartesian_plan, fraction = helper.plan_probing(z=0.06)

    helper.execute_plan(cartesian_plan)

    helper.disable_probing()

    input("\n-- Press ENTER to stop the demo and return to start --")

    helper.go_home()

    print("\n-- Helper demo complete! --")
  except rospy.ROSInterruptException:
    return
  except KeyboardInterrupt:
    return

if __name__ == '__main__':
  main()