"""Display-only use of the official O10 solver. This module opens no device."""
import math

JOINTS = ('thumb_roll','thumb_abad','thumb_mcp','thumb_pip','thumb_dip',
          'index_abad','index_pip','index_dip','middle_pip','middle_dip',
          'ring_abad','ring_pip','ring_dip','pinky_abad','pinky_pip','pinky_dip')

class PoseSolver:
    def __init__(self, hand='right'):
        if hand not in ('left','right'): raise ValueError('Unknown hand side')
        from omnihand.omnihand_2025 import OmniHand2025Solver
        self.solver=OmniHand2025Solver(hand=='left')
        self.prefix='L_' if hand=='left' else 'R_'

    def solve(self, positions):
        if len(positions)!=10 or any(type(v) is not int for v in positions):
            raise ValueError('Ten measured integer actuator positions required')
        # Preserve original telemetry. Clamp only the solver input to its documented domain.
        inputs=[max(0,min(4096,v)) for v in positions]
        active=list(self.solver.actuator_input_to_active_joint_pos(inputs))
        angles=list(self.solver.get_all_joint_angles(active))
        if len(active)!=10 or len(angles)!=16 or not all(math.isfinite(a) for a in angles):
            raise ValueError('Invalid official kinematics result')
        joints={self.prefix+k+'_joint':v for k,v in zip(JOINTS,angles)}
        joints[self.prefix+'middle_abad_joint']=0.0
        return {'joints_rad':joints, 'active_rad':active, 'source':'official_sdk_1.1.11',
                'clamped_for_display':[i+1 for i,(a,b) in enumerate(zip(positions,inputs)) if a!=b],
                'passive_joints':'calculated', 'error':None}
