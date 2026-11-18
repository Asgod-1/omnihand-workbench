export function shortestAngle(delta){return ((delta+Math.PI)%(2*Math.PI)+2*Math.PI)%(2*Math.PI)-Math.PI;}
export function cameraDistance(size,fovDegrees,aspect,padding=1.2){
  const vertical=fovDegrees*Math.PI/180,horizontal=2*Math.atan(Math.tan(vertical/2)*Math.max(.01,aspect));
  const fit=Math.max(size.x/(2*Math.tan(horizontal/2)),size.y/(2*Math.tan(vertical/2)));
  return Math.max(.08,(fit+size.z/2)*padding);
}
export function easeInOut(t){return t<.5?4*t*t*t:1-Math.pow(-2*t+2,3)/2;}
