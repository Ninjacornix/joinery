"""Blender side of the animation operations, part one: reading a rig and posing it.

Finds the role of each bone (Rigify FK controls, or a hand-made humanoid's bones by
name), the rig's facing, and poses one moment of each motion, with a two-bone leg
solver for planted feet. Loaded before blender_anim_edit.EDIT_CODE; both run inside
the scope guard. See joinery.tools.blender_anim.
"""

POSE_CODE = r'''
import math as _jm
import re as _jre
from mathutils import Vector as _JV, Quaternion as _JQ, Matrix as _JMx, Euler as _JE

_J_UP = _JV((0.0, 0.0, 1.0))
_J_SIDES = ('L', 'R')
_J_RIGIFY = {
    'root': 'torso', 'hips': 'hips', 'chest': 'chest', 'neck': 'neck', 'head': 'head',
    'thigh.L': 'thigh_fk.L', 'shin.L': 'shin_fk.L', 'foot.L': 'foot_fk.L',
    'thigh.R': 'thigh_fk.R', 'shin.R': 'shin_fk.R', 'foot.R': 'foot_fk.R',
    'upper_arm.L': 'upper_arm_fk.L', 'forearm.L': 'forearm_fk.L', 'hand.L': 'hand_fk.L',
    'upper_arm.R': 'upper_arm_fk.R', 'forearm.R': 'forearm_fk.R', 'hand.R': 'hand_fk.R',
}
_J_SWITCHES = ('thigh_parent.L', 'thigh_parent.R', 'upper_arm_parent.L', 'upper_arm_parent.R')
_J_LIMBS = (
    ('thigh', r'thigh|upleg|upperleg|legupper|femur'),
    ('forearm', r'forearm|lowerarm|armlower|elbow'),
    ('upper_arm', r'upperarm|armupper|humerus|^arm$'),
    ('shin', r'shin|calf|lowerleg|leglower|knee|^leg$'),
    ('foot', r'foot|ankle'),
    ('hand', r'hand|wrist'),
)
_J_CENTRE = (
    ('hips', r'^(hips?|pelvis)$'),
    ('chest', r'chest|^spine0?[2-9]$|^spine\.00[2-9]$|^spine_0?[2-9]$'),
    ('spine', r'^spine(0?1|\.001|_0?1)?$'),
    ('neck', r'^neck'),
    ('head', r'^head$'),
)
_J_GROUP_ROLES = {
    'root': ('root',), 'hips': ('hips',), 'spine': ('spine', 'chest'), 'head': ('neck', 'head'),
    'legs': tuple(r + '.' + s for r in ('thigh', 'shin', 'foot') for s in _J_SIDES),
    'arms': tuple(r + '.' + s for r in ('upper_arm', 'forearm', 'hand') for s in _J_SIDES),
    'left_leg': ('thigh.L', 'shin.L', 'foot.L'), 'right_leg': ('thigh.R', 'shin.R', 'foot.R'),
    'left_arm': ('upper_arm.L', 'forearm.L', 'hand.L'),
    'right_arm': ('upper_arm.R', 'forearm.R', 'hand.R'),
}

def _j_clean(name):
    name = name.lower()
    for prefix in ('mixamorig:', 'mixamorig_', 'def-', 'def_', 'bip01 ', 'bip001 ', 'armature|'):
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name

def _j_side(name):
    if _jre.search(r'(?:^|[^a-z])(?:l|left)(?:$|[^a-z])', name) or name.startswith('left'):
        return 'L'
    if _jre.search(r'(?:^|[^a-z])(?:r|right)(?:$|[^a-z])', name) or name.startswith('right'):
        return 'R'
    return ''

def _j_core(name):
    core = _jre.sub(r'(?:^|[^a-z])(?:l|r|left|right)(?=$|[^a-z])', '', name)
    core = _jre.sub(r'^(left|right)', '', core)
    return _jre.sub(r'[\s_.\-]+', '', core) if core else core

def _j_roles(rig):
    bones = rig.pose.bones
    if 'rig_id' in rig.data and bones.get('torso') is not None and bones.get('thigh_fk.L') is not None:
        return {role: name for role, name in _J_RIGIFY.items() if bones.get(name) is not None}, True
    found = {}
    def better(role, bone):
        old = found.get(role)
        return old is None or (bone.bone.use_deform, -len(bone.name)) > (bones[old].bone.use_deform, -len(old))
    for bone in bones:
        name = _j_clean(bone.name)
        side = _j_side(name)
        core = _j_core(name) if side else _jre.sub(r'[\s_\-]+', '', name)
        if side:
            for role, pattern in _J_LIMBS:
                if _jre.search(pattern, core) and 'twist' not in core and 'end' not in core:
                    key = role + '.' + side
                    if better(key, bone):
                        found[key] = bone.name
                    break
        else:
            for role, pattern in _J_CENTRE:
                if _jre.search(pattern, name.replace(' ', '')):
                    if better(role, bone):
                        found[role] = bone.name
                    break
    if 'hips' in found:
        found['root'] = found['hips']
    if 'chest' not in found and 'spine' in found:
        found['chest'] = found['spine']
    return found, False

def _j_armature(name):
    if name:
        rig = bpy.data.objects.get(name)
        if rig is None or rig.type != 'ARMATURE':
            raise RuntimeError(f'There is no armature named {name!r} in this model.')
        return rig
    rigs = [o for o in bpy.data.objects if o.type == 'ARMATURE']
    if len(rigs) != 1:
        raise RuntimeError('This model has ' + str(len(rigs)) + ' armatures. Name the armature: '
                           + ', '.join(o.name for o in rigs))
    return rigs[0]

class _JRig:
    def __init__(self, rig, need=()):
        self.rig = rig
        self.roles, self.rigify = _j_roles(rig)
        missing = [r for r in need if r not in self.roles]
        if missing:
            raise RuntimeError(
                f'{rig.name!r} has no bone for ' + ', '.join(missing) + '. Joinery finds Rigify FK '
                'controls, or bones named like hips, spine, chest, neck, head, thigh/upper_leg.L, '
                'shin/lower_leg.L, foot.L, upper_arm.L, forearm/lower_arm.L, hand.L (and .R).')
        self.scene = bpy.context.scene
        self.fps = self.scene.render.fps / self.scene.render.fps_base
        forward = _JV((0.0, 0.0, 0.0))
        for side in _J_SIDES:
            foot = self.bone('foot.' + side)
            if foot is not None:
                v = foot.bone.tail_local - foot.bone.head_local
                forward += _JV((v.x, v.y, 0.0))
        self.f = forward.normalized() if forward.length > 1e-4 else _JV((0.0, -1.0, 0.0))
        self.a = self.f.cross(_J_UP).normalized()
        self.left = _J_UP.cross(self.f).normalized()
        self.ankle = {}
        self.leg = 1.0
        for side in _J_SIDES:
            thigh, shin = self.bone('thigh.' + side), self.bone('shin.' + side)
            if thigh is not None and shin is not None:
                ankle = shin.bone.tail_local.copy()
                # Feet a little apart under the hips, whatever the rest pose's stance.
                spread = (ankle - thigh.bone.head_local).dot(self.left)
                ankle -= self.left * spread * 0.75
                self.ankle[side] = ankle
                self.leg = (thigh.bone.length + shin.bone.length)
        self.hip_height = {s: self.bone('thigh.' + s).bone.head_local.z - self.ankle[s].z
                           for s in self.ankle}

    def bone(self, role):
        name = self.roles.get(role)
        return self.rig.pose.bones.get(name) if name else None

    def side_sign(self, side):
        return 1.0 if side == 'L' else -1.0

def _j_paths(pb):
    mode = pb.rotation_mode
    if mode == 'QUATERNION':
        rot = ('rotation_quaternion', 4)
    elif mode == 'AXIS_ANGLE':
        rot = ('rotation_axis_angle', 4)
    else:
        rot = ('rotation_euler', 3)
    return rot, ('location', 3)

def _j_set_rotation(pb, q):
    mode = pb.rotation_mode
    if mode == 'QUATERNION':
        pb.rotation_quaternion = q
    elif mode == 'AXIS_ANGLE':
        axis, angle = q.to_axis_angle()
        pb.rotation_axis_angle = (angle, axis.x, axis.y, axis.z)
    else:
        pb.rotation_euler = q.to_euler(mode, pb.rotation_euler)


def _j_local(pb, rotation):
    rest = pb.bone.matrix_local.to_quaternion()
    return rest.inverted() @ rotation @ rest

def _j_local_offset(pb, offset):
    return pb.bone.matrix_local.to_3x3().inverted() @ offset

def _j_rot(axis, degrees):
    return _JQ(axis, _jm.radians(degrees))

def _j_smooth(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)

def _j_update():
    bpy.context.view_layer.update()

def _j_posed(st, pb):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    return st.rig.evaluated_get(depsgraph).pose.bones[pb.name]

class _JPose:
    """Holds the rig still and free of its action while Joinery poses it."""
    def __init__(self, st):
        self.st = st
        rig = st.rig
        self.data = rig.animation_data
        self.saved = None
        if self.data is not None:
            self.saved = (self.data.action, getattr(self.data, 'action_slot', None), self.data.use_nla)
        self.frame = st.scene.frame_current
        self.switches = {}
        for name in _J_SWITCHES:
            pb = rig.pose.bones.get(name)
            if st.rigify and pb is not None and 'IK_FK' in pb:
                self.switches[name] = pb['IK_FK']

    def __enter__(self):
        if self.data is not None:
            self.data.action = None
            self.data.use_nla = False
        for name in self.switches:
            self.st.rig.pose.bones[name]['IK_FK'] = 1.0
        return self

    def __exit__(self, *exc):
        rig = self.st.rig
        for pb in rig.pose.bones:
            pb.location = (0.0, 0.0, 0.0)
            pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
            pb.rotation_euler = (0.0, 0.0, 0.0)
            pb.rotation_axis_angle = (0.0, 0.0, 1.0, 0.0)
            pb.scale = (1.0, 1.0, 1.0)
        for name, value in self.switches.items():
            rig.pose.bones[name]['IK_FK'] = value
        if self.saved is not None:
            action, slot, nla = self.saved
            self.data.action = action
            if slot is not None and action is not None and hasattr(self.data, 'action_slot'):
                self.data.action_slot = slot
            self.data.use_nla = nla
        self.st.scene.frame_set(self.frame)
        return False

def _j_rest(st):
    for pb in st.rig.pose.bones:
        pb.location = (0.0, 0.0, 0.0)
        pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        pb.rotation_euler = (0.0, 0.0, 0.0)
        pb.rotation_axis_angle = (0.0, 0.0, 1.0, 0.0)

def _j_solve_leg(st, side, target, toe=0.0, keep=None):
    """Bend thigh and shin so the ankle reaches target (armature space).

    Swing and knee bend are solved in the leg's side view (forward and up), the
    hip turns the leg out or in for what is left sideways, and the foot keeps
    [keep] (an armature-space rotation), or lies flat, pitched by toe radians.
    """
    thigh, shin, foot = st.bone('thigh.' + side), st.bone('shin.' + side), st.bone('foot.' + side)
    f, u, a, out = st.f, _J_UP, st.a, st.left
    def angle(v):
        return _jm.atan2(v.dot(f), -v.dot(u))
    def flat(v):
        return _jm.hypot(v.dot(f), v.dot(u))
    v1 = thigh.bone.tail_local - thigh.bone.head_local
    v2 = shin.bone.tail_local - shin.bone.head_local
    l1, l2, a1, a2 = flat(v1), flat(v2), angle(v1), angle(v2)
    _j_set_rotation(thigh, _JQ())
    _j_update()
    hip = _j_posed(st, thigh).head.copy()
    goal = target.copy()
    spread = 0.0
    error = 0.0
    for _round in range(8):
        d = goal - hip
        dx, dz = d.dot(f), d.dot(u)
        reach = min(max(_jm.hypot(dx, dz), abs(l1 - l2) + 1e-4), (l1 + l2) * 0.9995)
        beta = _jm.atan2(dx, -dz)
        cosine = (l1 * l1 + reach * reach - l2 * l2) / (2.0 * l1 * reach)
        p1 = beta + _jm.acos(min(max(cosine, -1.0), 1.0))
        kx, kz = l1 * _jm.sin(p1), -l1 * _jm.cos(p1)
        ax, az = reach * _jm.sin(beta), -reach * _jm.cos(beta)
        p2 = _jm.atan2(ax - kx, -(az - kz))
        t1 = p1 - a1
        t2 = p2 - a2 - t1
        _j_set_rotation(thigh, _j_local(thigh, _JQ(f, spread) @ _JQ(a, t1)))
        _j_set_rotation(shin, _j_local(shin, _JQ(a, t2)))
        _j_update()
        miss = target - _j_posed(st, shin).tail
        error = miss.length
        if error < 2e-4:
            break
        goal = goal + f * miss.dot(f) + u * miss.dot(u)
        spread += miss.dot(out) / max(reach, 0.05)
    if foot is not None:
        if keep is None:
            keep = _JQ(a, toe) @ foot.bone.matrix_local.to_quaternion()
        posed = _j_posed(st, foot)
        foot.matrix = _JMx.LocRotScale(posed.head, keep, posed.matrix.to_scale())
        _j_update()
    return error

def _j_arm(st, side, swing, lower_to=None, flex=0.0, raise_by=0.0, forearm_raise=0.0, twist=0.0):
    upper, fore = st.bone('upper_arm.' + side), st.bone('forearm.' + side)
    if upper is None:
        return
    s = st.side_sign(side)
    v = upper.bone.tail_local - upper.bone.head_local
    out = _jm.degrees(_jm.atan2(abs(v.dot(st.left)), -v.dot(_J_UP)))
    lower = out - lower_to if lower_to is not None else 0.0
    if fore is not None:
        # Flex counts from a straight arm, whatever bend the rest pose has.
        w = fore.bone.tail_local - fore.bone.head_local
        flex -= _jm.degrees(v.angle(w)) if v.length and w.length else 0.0
    turn = _j_rot(st.a, swing) @ _j_rot(st.f, s * (raise_by - lower)) @ _j_rot(_J_UP, s * twist)
    _j_set_rotation(upper, _j_local(upper, turn))
    if fore is not None:
        _j_set_rotation(fore, _j_local(fore, _j_rot(st.a, flex) @ _j_rot(st.f, s * forearm_raise)))

def _j_spine(st, lean=0.0, twist_hips=0.0, twist_chest=0.0, side_bend=0.0, nod=0.0, look=0.0):
    hips, chest = st.bone('hips'), st.bone('chest')
    if hips is not None:
        _j_set_rotation(hips, _j_local(hips, _j_rot(_J_UP, twist_hips)))
        if hips == st.bone('root'):
            # A hand-made rig's hips carry the spine: the chest turns back by as much.
            twist_chest -= twist_hips
    if chest is not None:
        turn = _j_rot(_J_UP, twist_chest) @ _j_rot(st.f, side_bend) @ _j_rot(st.a, -lean)
        _j_set_rotation(chest, _j_local(chest, turn))
    neck, head = st.bone('neck'), st.bone('head')
    if neck is not None:
        _j_set_rotation(neck, _j_local(neck, _j_rot(st.a, lean * 0.4 + nod)))
    if head is not None:
        _j_set_rotation(head, _j_local(head, _j_rot(_J_UP, look) @ _j_rot(st.a, lean * 0.3)))

def _j_root(st, drop=0.0, sway=0.0, forward=0.0):
    root = st.bone('root')
    if root is None:
        return
    root.location = _j_local_offset(root, _J_UP * drop + st.left * sway + st.f * forward)

def _j_fk_leg(st, side, thigh_deg, shin_deg, foot_deg=0.0):
    thigh, shin, foot = st.bone('thigh.' + side), st.bone('shin.' + side), st.bone('foot.' + side)
    _j_set_rotation(thigh, _j_local(thigh, _j_rot(st.a, thigh_deg)))
    _j_set_rotation(shin, _j_local(shin, _j_rot(st.a, -shin_deg)))
    if foot is not None:
        _j_set_rotation(foot, _j_local(foot, _j_rot(st.a, foot_deg)))

_J_GAITS = {
    # duty (stance share), stride (half step, x leg), lift, bounce, arm swing, elbow, lean, twist
    'walk': (0.6, 0.33, 0.12, 0.022, 18.0, 15.0, 3.0, 5.0),
    'run': (0.36, 0.48, 0.32, 0.045, 32.0, 80.0, 10.0, 8.0),
}

def _j_gait_pose(st, kind, phase, p):
    duty, stride, lift, bounce, swing, elbow, lean, twist = _J_GAITS[kind]
    leg = st.leg
    half = leg * stride * p.get('stride', 1.0)
    height = leg * lift * max(p.get('stride', 1.0), 0.3)
    hop = leg * bounce * p.get('bounce', 1.0)
    hip = min(st.hip_height.values())
    reach = 0.985 * leg
    drop = hip - _jm.sqrt(max(reach * reach - half * half, 0.01))
    wave = _jm.cos(4.0 * _jm.pi * (phase - duty / 2.0))
    bob = hop * (wave - 1.0) if kind == 'walk' else -hop * (wave + 1.0)
    sway = (0.012 * leg * _jm.sin(2.0 * _jm.pi * phase)) if kind == 'walk' else 0.0
    left_forward = _jm.cos(2.0 * _jm.pi * phase)
    _j_rest(st)
    _j_root(st, drop=-drop + bob, sway=sway)
    lean_deg = p.get('lean', lean)
    _j_spine(st, lean=lean_deg, twist_hips=-twist * left_forward, twist_chest=twist * 0.8 * left_forward)
    arms = swing * p.get('arm_swing', 1.0)
    for side, sign in (('L', 1.0), ('R', -1.0)):
        angle = -arms * left_forward * sign
        _j_arm(st, side, angle, lower_to=12.0, flex=elbow + 0.25 * elbow * (angle / arms if arms else 0.0))
    _j_update()
    targets = {}
    if kind == 'walk':
        # An inverted pendulum: the hips ride as high as the planted legs allow, so the
        # stance leg is nearly straight; bounce scales that rise and fall.
        planted = []
        for offset in (0.0, 0.5):
            q = (phase + offset) % 1.0
            if q < duty:
                planted.append(half - 2.0 * half * q / duty)
        reach = 0.975 * leg
        raw = min(_jm.sqrt(max(reach * reach - x * x, 0.01)) for x in planted) - hip
        mean = _jm.sqrt(max(reach * reach - half * half / 3.0, 0.01)) - hip
        _j_root(st, drop=mean + (raw - mean) * p.get('bounce', 1.0), sway=sway)
        _j_update()
    for side, offset in (('L', 0.0), ('R', 0.5)):
        q = (phase + offset) % 1.0
        if q < duty:
            x, z, toe = half - 2.0 * half * q / duty, 0.0, 0.0
        else:
            s = (q - duty) / (1.0 - duty)
            x = -half + 2.0 * half * _j_smooth(s)
            z = height * _jm.sin(_jm.pi * s)
            toe = _jm.radians(-12.0) * _jm.sin(_jm.pi * s)
        targets[side] = (st.ankle[side] + st.f * x + _J_UP * z, toe)
    for side, (target, toe) in targets.items():
        _j_solve_leg(st, side, target, toe)
    ground = 2.0 * half / (duty * st.seconds)
    return ground

def _j_still_feet(st):
    for side in _J_SIDES:
        if side in st.ankle:
            _j_solve_leg(st, side, st.ankle[side])

def _j_pose(st, motion, phase, p):
    """Pose the rig for one moment of a motion; returns the ground speed (m/s)."""
    if motion in _J_GAITS:
        return _j_gait_pose(st, motion, phase, p)
    leg = st.leg
    _j_rest(st)
    lean = p.get('lean', 0.0)
    arms = p.get('arm_swing', 1.0)
    hop = p.get('bounce', 1.0)
    if motion == 'idle':
        breath = _jm.sin(2.0 * _jm.pi * phase)
        _j_root(st, drop=-0.006 * leg - 0.008 * leg * hop * (1.0 - _jm.cos(2.0 * _jm.pi * phase)) / 2.0,
                sway=0.01 * leg * _jm.sin(2.0 * _jm.pi * phase + 0.6))
        _j_spine(st, lean=lean - 1.2 * breath, look=2.5 * _jm.sin(2.0 * _jm.pi * phase + 1.0))
        for side in _J_SIDES:
            _j_arm(st, side, 2.0 * arms * breath, lower_to=10.0, flex=8.0)
        _j_update()
        _j_still_feet(st)
        return 0.0
    if motion == 'jump_start':
        crouch = _j_smooth(phase / 0.7)
        push = _j_smooth((phase - 0.7) / 0.3)
        depth = 0.18 * leg * hop * crouch * (1.0 - push)
        _j_root(st, drop=-depth + 0.03 * leg * push)
        _j_spine(st, lean=lean + 25.0 * crouch * (1.0 - push) + 5.0 * push)
        for side in _J_SIDES:
            _j_arm(st, side, arms * (-45.0 * crouch * (1.0 - push) + 100.0 * push), lower_to=15.0, flex=20.0)
        _j_update()
        _j_still_feet(st)
        return 0.0
    if motion == 'jump_air':
        w = _jm.sin(2.0 * _jm.pi * phase)
        _j_root(st, drop=0.0)
        _j_spine(st, lean=lean + 5.0)
        _j_fk_leg(st, 'L', 45.0 + 5.0 * w, 75.0 + 5.0 * w, 15.0)
        _j_fk_leg(st, 'R', 30.0 - 5.0 * w, 60.0 - 5.0 * w, 10.0)
        for side, sign in (('L', 1.0), ('R', -1.0)):
            _j_arm(st, side, arms * (60.0 + 8.0 * w * sign), lower_to=25.0, flex=30.0)
        return 0.0
    if motion == 'jump_land':
        hit = _j_smooth(phase / 0.25) if phase < 0.25 else 1.0 - _j_smooth((phase - 0.25) / 0.75)
        _j_root(st, drop=-0.2 * leg * hop * hit)
        _j_spine(st, lean=lean + 20.0 * hit)
        for side in _J_SIDES:
            _j_arm(st, side, arms * (50.0 * (1.0 - _j_smooth(phase)) - 15.0 * hit), lower_to=15.0, flex=25.0)
        _j_update()
        _j_still_feet(st)
        return 0.0
    side = 'L' if p.get('side') == 'left' else 'R'
    other = 'R' if side == 'L' else 'L'
    s = st.side_sign(side)
    if motion in ('attack', 'swing'):
        wind = _j_smooth(phase / 0.4)
        strike = _j_smooth((phase - 0.4) / 0.15)
        back = _j_smooth((phase - 0.55) / 0.45)
        k = wind * (1.0 - strike)
        m = strike * (1.0 - back)
        _j_root(st, drop=-0.05 * leg * hop * (k + m))
        _j_spine(st, lean=lean + 10.0 * m, twist_chest=s * 20.0 * k - s * 25.0 * m)
        _j_arm(st, side, arms * (150.0 * k + 40.0 * m), lower_to=20.0, flex=100.0 * k + 10.0 * m)
        _j_arm(st, other, arms * 20.0 * (k + m), lower_to=15.0, flex=10.0 + 50.0 * (k + m))
        _j_update()
        _j_still_feet(st)
        return 0.0
    if motion == 'wave':
        w = _jm.sin(2.0 * _jm.pi * 2.0 * phase)
        _j_root(st, sway=-s * 0.01 * leg)
        _j_spine(st, lean=lean, side_bend=-s * 3.0, look=-s * 8.0)
        _j_arm(st, side, 10.0, lower_to=150.0, flex=0.0, forearm_raise=20.0 + 25.0 * arms * w)
        _j_arm(st, other, 0.0, lower_to=10.0, flex=8.0)
        _j_update()
        _j_still_feet(st)
        return 0.0
    raise RuntimeError(f'Unknown motion {motion!r}.')

_J_NEEDS = {
    'arms': tuple(r + '.' + s for r in ('upper_arm', 'forearm') for s in _J_SIDES),
    'legs': tuple(r + '.' + s for r in ('thigh', 'shin') for s in _J_SIDES),
}

def _j_keyed(st):
    """Pose bones each action keys: every role bone, plus Rigify's IK/FK switches."""
    names = list(dict.fromkeys(st.roles.values()))
    return [st.rig.pose.bones[n] for n in names]

def _j_sample(st, samples, keyed):
    for pb in keyed:
        (rot, count), (loc, _three) = _j_paths(pb)
        values = list(getattr(pb, rot))
        for index in range(count):
            samples.setdefault((pb.name, rot, index), []).append(values[index])
        for index in range(3):
            samples.setdefault((pb.name, loc, index), []).append(pb.location[index])

def _j_channelbag(action, rig):
    from bpy_extras import anim_utils
    slot = action.slots[0] if len(action.slots) else action.slots.new(id_type='OBJECT', name=rig.name)
    return slot, anim_utils.action_ensure_channelbag_for_slot(action, slot)

def _j_curves(action):
    from bpy_extras import anim_utils
    found = []
    for slot in action.slots:
        bag = anim_utils.action_get_channelbag_for_slot(action, slot)
        if bag is not None:
            found += list(bag.fcurves)
    return found

def _j_write(curve, start, values):
    points = curve.keyframe_points
    points.clear()
    points.add(len(values))
    co = []
    for index, value in enumerate(values):
        co += [float(start + index), float(value)]
    points.foreach_set('co', co)
    for point in points:
        point.interpolation = 'LINEAR'
    curve.update()

def _j_assign(rig, action):
    data = rig.animation_data or rig.animation_data_create()
    data.action = action
    if hasattr(data, 'action_slot') and len(action.slots) and data.action_slot is None:
        data.action_slot = action.slots[0]

def _j_stash(rig, action):
    data = rig.animation_data or rig.animation_data_create()
    for track in data.nla_tracks:
        for strip in track.strips:
            if strip.action == action:
                track.name = action.name
                strip.name = action.name
                return
    track = data.nla_tracks.new()
    track.name = action.name
    strip = track.strips.new(action.name, int(action.frame_range[0]), action)
    if hasattr(strip, 'action_slot') and len(action.slots) and strip.action_slot is None:
        strip.action_slot = action.slots[0]
    track.mute = True

def _j_unstash(rig, action):
    data = rig.animation_data
    if data is None:
        return
    for track in list(data.nla_tracks):
        strips = [s for s in track.strips if s.action == action]
        for strip in strips:
            track.strips.remove(strip)
        if strips and not len(track.strips):
            data.nla_tracks.remove(track)
    if data.action == action:
        data.action = None

def _j_new_action(st, name, replace):
    old = bpy.data.actions.get(name)
    if old is not None:
        if not replace:
            raise RuntimeError(f'There is already an action named {name!r}. Edit it with edit_action, '
                               'or pass replace=true to make it again from scratch.')
        _j_unstash(st.rig, old)
        bpy.data.actions.remove(old)
    action = bpy.data.actions.new(name)
    action.use_fake_user = True
    return action

def _j_make(st, motion, name, p):
    loop = bool(p.get('loop', _J_LOOPS[motion]))
    seconds = p.get('length', _J_SECONDS[motion] / p.get('speed', 1.0))
    frames = max(2, int(round(seconds * st.fps)))
    st.seconds = frames / st.fps
    keyed = _j_keyed(st)
    samples = {}
    ground = 0.0
    with _JPose(st):
        for index in range(frames + 1):
            phase = index / frames
            ground = _j_pose(st, motion, phase if loop else min(phase, 1.0), p)
            _j_update()
            _j_sample(st, samples, keyed)
    action = _j_new_action(st, name, bool(p.get('replace', False)))
    slot, bag = _j_channelbag(action, st.rig)
    # Frame 0, so the game's clip starts at 0 s and a loop's last key meets its first.
    start = 0
    for (bone, prop, index), values in samples.items():
        curve = bag.fcurves.new(f'pose.bones["{bone}"].{prop}', index=index, group_name=bone)
        _j_write(curve, start, values)
    for switch in _J_SWITCHES if st.rigify else ():
        if st.rig.pose.bones.get(switch) is not None:
            curve = bag.fcurves.new(f'pose.bones["{switch}"]["IK_FK"]', index=0, group_name=switch)
            _j_write(curve, start, [1.0] * (frames + 1))
    action.use_frame_range = True
    action.frame_start, action.frame_end = start, start + frames
    action.use_cyclic = loop
    action['joinery_kind'] = motion
    action['joinery_loop'] = loop
    action['joinery_ground_speed'] = round(ground, 4)
    action['joinery_params'] = _joinery_json.dumps(p, sort_keys=True)
    _j_stash(st.rig, action)
    return action

'''
