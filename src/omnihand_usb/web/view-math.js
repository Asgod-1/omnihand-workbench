export function shortestAngle(delta){return ((delta+Math.PI)%(2*Math.PI)+2*Math.PI)%(2*Math.PI)-Math.PI;}
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
