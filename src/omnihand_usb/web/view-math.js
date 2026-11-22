export function shortestAngle(delta){return ((delta+Math.PI)%(2*Math.PI)+2*Math.PI)%(2*Math.PI)-Math.PI;}
export function modelJointAngle(name, radians) {
  if (name !== 'R_thumb_abad_joint') return radians;
  // The O10 SDK's right-hand endpoints are -1.64 and 0.05 rad. Match
  // the reversed travel to the pinned right-hand URDF's own zero and limits.
  const travel = Math.min(1, Math.max(0, (0.05 - radians) / (0.05 + 1.64)));
  const minimum = -1.464235482612666;
  const maximum = 0.04537856055185257;
  return minimum + (maximum - minimum) * travel;
}
// Points are in camera axes, with positive z toward the camera.
export function fitCameraFrame(points, fovDegrees, aspect, near) {
  const min = {...points[0]}, max = {...points[0]};
  for (const {x, y, z} of points) {
    min.x = Math.min(min.x, x); max.x = Math.max(max.x, x);
    min.y = Math.min(min.y, y); max.y = Math.max(max.y, y);
    min.z = Math.min(min.z, z); max.z = Math.max(max.z, z);
  }
  const center = {x: (min.x + max.x) / 2, y: (min.y + max.y) / 2, z: (min.z + max.z) / 2};
  const vertical = Math.tan(fovDegrees * Math.PI / 360) * 0.9;
  const horizontal = vertical * aspect;
  let distance = max.z - center.z + near;
  for (const {x, y, z} of points) {
    const fit = Math.max(Math.abs(x - center.x) / horizontal, Math.abs(y - center.y) / vertical);
    distance = Math.max(distance, z - center.z + fit);
  }
  return {center, distance};
}
export function easeInOut(t){return t<.5?4*t*t*t:1-Math.pow(-2*t+2,3)/2;}
