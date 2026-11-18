"""Python control through the running dashboard's single device connection.

No SDK/adapter is opened here. Constructing the client only stores an address.
"""
from __future__ import annotations
import json
import time
import urllib.error
import urllib.request

from .motion_protocol import target_vector
from .control import duration_value,execution_mode_value
from .control_profile import control_profile,joint_target


class ControlClient:
    def __init__(self,port=8765,timeout=12):
        self.base=f'http://127.0.0.1:{int(port)}';self.timeout=timeout;self.session_id=None

    def _request(self,path,body=None):
        request=urllib.request.Request(self.base+path)
        if body is not None:
            request=urllib.request.Request(self.base+path,json.dumps(body,allow_nan=False).encode('utf-8'),{'Content-Type':'application/json'},method='POST')
        try:
            with urllib.request.urlopen(request,timeout=self.timeout) as response:return json.load(response)
        except urllib.error.HTTPError as exc:
            try:message=json.load(exc).get('error',str(exc))
            except (ValueError,AttributeError):message=str(exc)
            raise RuntimeError(message) from exc

    def state(self):return self._request('/api/state')
    def catalog(self):return self._request('/api/control')

    @staticmethod
    def _target(preset,positions):
        if (preset is None)==(positions is None):raise ValueError('Provide one preset or ten positions')
        return {'preset_id':preset} if preset is not None else {'positions':target_vector(positions)}

    def preview(self,*,preset=None,positions=None):
        return self._request('/api/control',{'action':'preview',**self._target(preset,positions)})

    def arm(self):
        # A reconnect invalidates this session; later writes must never silently re-arm.
        self.session_id=self.state()['control']['session_id']
        return self._action('arm')

    def _action(self,action,**body):
        if not self.session_id:raise RuntimeError('Call arm() explicitly before control operations')
        return self._request('/api/control',{'action':action,'session_id':self.session_id,**body})

    def enable(self):return self._action('enable')
    def disable(self):return self._action('disable')
    def stop(self):return self._action('stop')
    def lock(self):return self._action('lock')
    def move(self,*,preset=None,positions=None,mode='direct',duration=4,control_mode='position',currents_ma=None,velocities=None):
        """Send one full target by default; duration applies only to mode='smooth'."""
        profile=control_profile({'control_mode':control_mode,'currents_ma':currents_ma,'velocities':velocities})
        body={'execution_mode':execution_mode_value(mode),**self._target(preset,positions),**profile}
        if mode=='smooth':body['duration']=duration_value(duration)
        return self._action('move',**body)

    def joint(self,index,position,*,control_mode='position',currents_ma=None,velocities=None):
        """Control one 1-based motor without writing targets for the other nine."""
        joint_target(index,position)
        profile=control_profile({'control_mode':control_mode,'currents_ma':currents_ma,'velocities':velocities})
        return self._action('joint',index=index,position=position,**profile)

    def wait(self,motion_id,timeout=45):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            state=self.state()['control']
            if state['session_id']!=self.session_id:raise RuntimeError('Device session changed while waiting')
            if state['motion_id']!=motion_id:raise RuntimeError('Motion was replaced')
            if not state['running']:
                if state['status']!='completed':raise RuntimeError(state.get('error') or state['message'])
                return state
            time.sleep(.2)
        # Do not automatically replay a command whose execution is uncertain.
        raise TimeoutError('Motion wait timed out; inspect feedback or call stop()')
