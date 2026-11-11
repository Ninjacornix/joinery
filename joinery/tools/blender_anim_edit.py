"""Blender side of the animation operations, part two: actions.

Writes an action from sampled poses, applies typed edits to one (retime,
amplitude, bounce, offset, foot_lock, loop_seam, mirror, rename, copy, delete),
measures one, and the entry points the builders in joinery.tools.blender_anim
call. Runs after blender_anim_pose.POSE_CODE.
"""

EDIT_CODE = r'''_J_LOOPS = LOOPS_HERE
_J_SECONDS = SECONDS_HERE

def _j_frames(action):
    start, end = action.frame_range
    return int(round(start)), int(round(end))

def _j_apply_frame(st, curves, frame):
    """Pose the rig from an action's curves at one frame, without assigning the action."""
    for curve in curves:
        path = curve.data_path
        if not path.startswith('pose.bones["'):
            continue
        bone = path.split('"')[1]
        pb = st.rig.pose.bones.get(bone)
        if pb is None:
            continue
        rest = path[len('pose.bones["' + bone + '"]'):]
        value = curve.evaluate(frame)
        if rest.startswith('["'):
            pb[rest[2:-2]] = value
        elif rest.startswith('.'):
            try:
                getattr(pb, rest[1:])[curve.array_index] = value
            except (AttributeError, TypeError, IndexError):
                pass

def _j_bone_curves(curves, pb):
    head = f'pose.bones["{pb.name}"].'
    return [c for c in curves if c.data_path.startswith(head)]

def _j_bones_for(st, bones):
    if isinstance(bones, list):
        missing = [b for b in bones if st.rig.pose.bones.get(b) is None]
        if missing:
            raise RuntimeError(f'{st.rig.name!r} has no bone named ' + ', '.join(map(repr, missing)) + '.')
        return [st.rig.pose.bones[b] for b in bones]
    if bones == 'all':
        return _j_keyed(st)
    roles = _J_GROUP_ROLES[bones]
    found = [st.bone(r) for r in roles if st.bone(r) is not None]
    if not found:
        raise RuntimeError(f'{st.rig.name!r} has no bones for {bones}.')
    return list(dict.fromkeys(found))

def _j_resample(curves, start, end, frames, mapping):
    for curve in curves:
        values = [curve.evaluate(mapping(k)) for k in range(frames + 1)]
        _j_write(curve, start, values)

def _j_retime(st, action, op):
    start, end = _j_frames(action)
    span = end - start
    if 'length' in op:
        frames = max(1, int(round(op['length'] * st.fps)))
    else:
        frames = max(1, int(round(span / op['speed'])))
    _j_resample(_j_curves(action), start, end, frames, lambda k: start + k * span / frames)
    action.frame_start, action.frame_end = start, start + frames
    return {'frames': frames}

def _j_quat_power(q, factor):
    axis, angle = q.to_axis_angle()
    if angle > _jm.pi:
        angle -= 2.0 * _jm.pi
    return _JQ(axis, angle * factor)

def _j_amplitude(st, action, op):
    start, end = _j_frames(action)
    channel = op.get('channel', 'rotation')
    factor = op['factor']
    curves = _j_curves(action)
    changed = 0
    for pb in _j_bones_for(st, op.get('bones', 'all')):
        mine = _j_bone_curves(curves, pb)
        for prop, size in (('rotation_quaternion', 4), ('rotation_euler', 3), ('rotation_axis_angle', 4), ('location', 3)):
            if (prop == 'location') != (channel == 'location') and channel != 'all':
                continue
            parts = {c.array_index: c for c in mine if c.data_path.endswith('.' + prop)}
            if len(parts) != size:
                continue
            frames = range(start, end + 1)
            rows = [[parts[i].evaluate(f) for i in range(size)] for f in frames]
            if prop == 'rotation_quaternion':
                quats = [_JQ(r).normalized() for r in rows]
                for i in range(1, len(quats)):
                    if quats[i].dot(quats[i - 1]) < 0:
                        quats[i].negate()
                mean = _JQ([sum(q[i] for q in quats) / len(quats) for i in range(4)]).normalized()
                rows = [list(mean @ _j_quat_power(mean.inverted() @ q, factor)) for q in quats]
            else:
                means = [sum(r[i] for r in rows) / len(rows) for i in range(size)]
                rows = [[means[i] + (r[i] - means[i]) * factor for i in range(size)] for r in rows]
            for i in range(size):
                _j_write(parts[i], start, [r[i] for r in rows])
            changed += 1
    if not changed:
        raise RuntimeError(f'{action.name!r} does not key those bones ({op.get("bones", "all")}).')
    return {'channels': changed}

def _j_root_heights(st, action):
    root = st.bone('root')
    curves = [c for c in _j_bone_curves(_j_curves(action), root) if c.data_path.endswith('.location')] if root else []
    if len(curves) != 3:
        raise RuntimeError(f'{action.name!r} does not move the body ({st.roles.get("root", "no root bone")}).')
    up = root.bone.matrix_local.to_3x3().inverted() @ _J_UP
    return root, sorted(curves, key=lambda c: c.array_index), up

def _j_bounce(st, action, op):
    start, end = _j_frames(action)
    root, curves, up = _j_root_heights(st, action)
    rows = [_JV([c.evaluate(f) for c in curves]) for f in range(start, end + 1)]
    heights = [r.dot(up) for r in rows]
    mean = sum(heights) / len(heights)
    rows = [r + up * ((mean + (h - mean) * op['factor']) - h) for r, h in zip(rows, heights)]
    for i, curve in enumerate(curves):
        _j_write(curve, start, [r[i] for r in rows])
    result = {'bounce_factor': op['factor']}
    if all(('thigh.' + s) in st.roles and ('shin.' + s) in st.roles for s in _J_SIDES):
        result.update(_j_foot_lock(st, action, {}))
    return result

def _j_offset(st, action, op):
    start, end = _j_frames(action)
    curves = _j_curves(action)
    if 'frames' in op:
        span = end - start
        shift = op['frames'] % span if span else 0
        loop = bool(action.get('joinery_loop', action.use_cyclic))
        if loop:
            _j_resample(curves, start, end, span, lambda k: start + ((k + shift) % span))
        else:
            for curve in curves:
                for point in curve.keyframe_points:
                    point.co.x += op['frames']
                    point.handle_left.x += op['frames']
                    point.handle_right.x += op['frames']
                curve.update()
            action.frame_start, action.frame_end = start + op['frames'], end + op['frames']
    if 'location' in op or 'rotation_degrees' in op:
        for pb in _j_bones_for(st, op['bones']):
            mine = _j_bone_curves(curves, pb)
            if 'location' in op:
                local = _j_local_offset(pb, _JV(op['location']))
                for c in mine:
                    if c.data_path.endswith('.location'):
                        _j_write(c, start, [c.evaluate(f) + local[c.array_index] for f in range(start, end + 1)])
            if 'rotation_degrees' in op:
                x, y, z = (_jm.radians(v) for v in op['rotation_degrees'])
                turn = _j_local(pb, _JMx.Rotation(z, 3, 'Z').to_quaternion() @ _JMx.Rotation(y, 3, 'Y').to_quaternion() @ _JMx.Rotation(x, 3, 'X').to_quaternion())
                (rot, size), _loc = _j_paths(pb)
                parts = {c.array_index: c for c in mine if c.data_path.endswith('.' + rot)}
                if len(parts) != size:
                    raise RuntimeError(f'{action.name!r} does not key the rotation of {pb.name!r}.')
                rows = []
                for f in range(start, end + 1):
                    with_values = [parts[i].evaluate(f) for i in range(size)]
                    if rot == 'rotation_quaternion':
                        q = _JQ(with_values)
                    elif rot == 'rotation_axis_angle':
                        q = _JQ(_JV(with_values[1:]), with_values[0])
                    else:
                        q = _JE(with_values, pb.rotation_mode).to_quaternion()
                    q = q @ turn
                    if rot == 'rotation_quaternion':
                        rows.append(list(q))
                    elif rot == 'rotation_axis_angle':
                        axis, angle = q.to_axis_angle()
                        rows.append([angle, *axis])
                    else:
                        rows.append(list(q.to_euler(pb.rotation_mode)))
                for i in range(size):
                    _j_write(parts[i], start, [r[i] for r in rows])
    return {}

def _j_ankles(st, action, curves):
    start, end = _j_frames(action)
    found = {s: [] for s in _J_SIDES if s in st.ankle}
    for frame in range(start, end + 1):
        _j_rest(st)
        _j_apply_frame(st, curves, frame)
        _j_update()
        for side in found:
            found[side].append(_j_posed(st, st.bone('shin.' + side)).tail.copy())
    return found

def _j_spans(flags, loop):
    spans, current = [], []
    for index, flag in enumerate(flags):
        if flag:
            current.append(index)
        elif current:
            spans.append(current)
            current = []
    if current:
        spans.append(current)
    if loop and len(spans) > 1 and spans[0][0] == 0 and spans[-1][-1] == len(flags) - 1:
        spans[0] = spans.pop() + spans[0]
    return spans

def _j_unwrap(span, count):
    """Frame indices of a contact span as moments in order: a span across a loop's seam
    continues past the last frame instead of jumping back to the first."""
    times, offset, previous = [], 0, None
    for index in span:
        if previous is not None and index < previous:
            offset += count - 1
        times.append(index + offset)
        previous = index
    return times

def _j_foot_lock(st, action, op):
    missing = [r for r in _J_NEEDS['legs'] if r not in st.roles]
    if missing:
        raise RuntimeError(f'{st.rig.name!r} has no leg bones for ' + ', '.join(missing) + '.')
    curves = _j_curves(action)
    for side in _J_SIDES:
        if not _j_bone_curves(curves, st.bone('thigh.' + side)):
            raise RuntimeError(f'{action.name!r} does not move the legs through '
                               f'{st.roles["thigh." + side]!r}, so their feet cannot be pinned.')
    start, end = _j_frames(action)
    loop = bool(action.get('joinery_loop', action.use_cyclic))
    speed = float(action.get('joinery_ground_speed', 0.0))
    threshold = op.get('threshold', 0.02)
    pinned = {}
    with _JPose(st):
        ankles = _j_ankles(st, action, curves)
        for side, points in ankles.items():
            count = len(points)
            ground = min(p.z for p in points)
            flags = [p.z <= ground + threshold for p in points]
            plan = {}
            for span in _j_spans(flags, loop):
                times = _j_unwrap(span, count)
                moved = [points[i] + st.f * (speed * t / st.fps) for i, t in zip(span, times)]
                anchor = sum(moved, _JV()) / len(moved)
                for i, t in zip(span, times):
                    target = anchor - st.f * (speed * t / st.fps)
                    plan[i] = (_JV((target.x, target.y, ground)), 1.0)
                for edge, edge_time, step in ((span[0], times[0], -1), (span[-1], times[-1], 1)):
                    for distance, weight in ((1, 0.66), (2, 0.33)):
                        j = edge + step * distance
                        if loop:
                            j %= count - 1
                        if 0 <= j < count and j not in plan:
                            goal = plan[edge][0] - st.f * (speed * step * distance / st.fps)
                            plan[j] = (points[j].lerp(goal, weight), weight)
                if loop and 0 in plan and (count - 1) not in plan:
                    plan[count - 1] = plan[0]
                if loop and (count - 1) in plan and 0 not in plan:
                    plan[0] = plan[count - 1]
            pinned[side] = plan
        rows = {}
        for side, plan in pinned.items():
            keep = [st.bone(r + '.' + side) for r in ('thigh', 'shin', 'foot') if st.bone(r + '.' + side) is not None]
            for index, (target, _w) in sorted(plan.items()):
                _j_rest(st)
                _j_apply_frame(st, curves, start + index)
                _j_update()
                foot = st.bone('foot.' + side)
                kept = _j_posed(st, foot).matrix.to_quaternion() if foot is not None else None
                _j_solve_leg(st, side, target, keep=kept)
                for pb in keep:
                    (rot, size), _loc = _j_paths(pb)
                    values = list(getattr(pb, rot))
                    for i in range(size):
                        rows.setdefault((pb.name, rot, i), {})[start + index] = values[i]
        for (bone, rot, i), frames in rows.items():
            path = f'pose.bones["{bone}"].{rot}'
            curve = next((c for c in curves if c.data_path == path and c.array_index == i), None)
            if curve is None:
                continue
            values = [frames.get(f, curve.evaluate(f)) for f in range(start, end + 1)]
            _j_write(curve, start, values)
    return {'pinned_frames': {s: len(p) for s, p in pinned.items()}}

def _j_loop_seam(st, action, op):
    start, end = _j_frames(action)
    span = max(end - start, 1)
    worst = 0.0
    curves = _j_curves(action)
    quats = {}
    for curve in curves:
        if curve.data_path.endswith('.rotation_quaternion'):
            quats.setdefault(curve.data_path, {})[curve.array_index] = curve
    for path, parts in quats.items():
        if len(parts) == 4:
            first = _JQ([parts[i].evaluate(start) for i in range(4)])
            last = _JQ([parts[i].evaluate(end) for i in range(4)])
            if first.dot(last) < 0:
                for curve in parts.values():
                    _j_write(curve, start, [-curve.evaluate(f) if f == end else curve.evaluate(f) for f in range(start, end + 1)])
    for curve in curves:
        values = [curve.evaluate(f) for f in range(start, end + 1)]
        error = values[-1] - values[0]
        worst = max(worst, abs(error))
        _j_write(curve, start, [v - error * i / span for i, v in enumerate(values)])
    action.use_cyclic = True
    action['joinery_loop'] = True
    return {'seam_fixed': round(worst, 6)}

def _j_mirror(st, action, op):
    curves = _j_curves(action)
    for curve in curves:
        if not curve.data_path.startswith('pose.bones["'):
            continue
        bone = curve.data_path.split('"')[1]
        flipped = bpy.utils.flip_name(bone)
        prop = curve.data_path.rsplit('.', 1)[-1]
        negate = (prop == 'location' and curve.array_index == 0) or (
            prop in ('rotation_quaternion', 'rotation_axis_angle') and curve.array_index in (2, 3)) or (
            prop == 'rotation_euler' and curve.array_index in (1, 2))
        if negate:
            for point in curve.keyframe_points:
                point.co.y = -point.co.y
                point.handle_left.y = -point.handle_left.y
                point.handle_right.y = -point.handle_right.y
        if flipped != bone:
            curve.data_path = curve.data_path.replace(f'["{bone}"]', f'["\x00{flipped}"]', 1)
    for curve in curves:
        curve.data_path = curve.data_path.replace('\x00', '')
        curve.update()
    return {}

def _j_rename(st, action, op):
    name = op['name']
    if bpy.data.actions.get(name) is not None:
        raise RuntimeError(f'There is already an action named {name!r}.')
    was = action.name
    action.name = name
    _j_stash(st.rig, action)
    return {'was': was}

def _j_copy(st, action, op):
    name = op['name']
    if bpy.data.actions.get(name) is not None:
        raise RuntimeError(f'There is already an action named {name!r}.')
    copy = action.copy()
    copy.name = name
    copy.use_fake_user = True
    _j_stash(st.rig, copy)
    return {'copy': copy.name}

_J_EDITS = {
    'retime': _j_retime, 'amplitude': _j_amplitude, 'bounce': _j_bounce, 'offset': _j_offset,
    'foot_lock': _j_foot_lock, 'loop_seam': _j_loop_seam, 'mirror': _j_mirror,
    'rename': _j_rename, 'copy': _j_copy,
}

def _j_measure(st, action, threshold=0.01):
    """Length, loop seam, bounce and foot slide during contact of one action."""
    start, end = _j_frames(action)
    loop = bool(action.get('joinery_loop', action.use_cyclic))
    speed = float(action.get('joinery_ground_speed', 0.0))
    data = st.rig.animation_data or st.rig.animation_data_create()
    saved = (data.action, data.use_nla)
    deform = [b.name for b in st.rig.data.bones if b.use_deform]
    heights, ankles, first, last = [], {s: [] for s in st.ankle}, {}, {}
    try:
        data.use_nla = False
        _j_assign(st.rig, action)
        for frame in range(start, end + 1):
            st.scene.frame_set(frame)
            posed = st.rig.evaluated_get(bpy.context.evaluated_depsgraph_get()).pose.bones
            root = st.bone('hips') if st.bone('hips') is not None else st.bone('root')
            if root is not None:
                heights.append(posed[root.name].head.z)
            for side in ankles:
                ankles[side].append(posed[st.roles['shin.' + side]].tail.copy())
            if frame in (start, end):
                (first if frame == start else last).update({n: posed[n].head.copy() for n in deform})
    finally:
        data.action, data.use_nla = saved
        _j_rest(st)
        st.scene.frame_set(st.scene.frame_start)
    slide, contact = 0.0, 0
    for side, points in ankles.items():
        if not points:
            continue
        ground = min(p.z for p in points)
        for span in _j_spans([p.z <= ground + threshold for p in points], loop):
            contact += len(span)
            times = _j_unwrap(span, len(points))
            moved = [points[i] + st.f * (speed * t / st.fps) for i, t in zip(span, times)]
            for one in moved:
                for other in moved:
                    flat = one - other
                    slide = max(slide, _jm.hypot(flat.x, flat.y))
    seam = max(((first[n] - last[n]).length for n in first), default=0.0)
    stride = 0.0
    for points in ankles.values():
        along = [p.dot(st.f) for p in points]
        if along:
            stride = max(stride, max(along) - min(along))
    return {
        'action': action.name, 'armature': st.rig.name, 'kind': str(action.get('joinery_kind', '')),
        'frames': [start, end], 'fps': round(st.fps, 3), 'seconds': round((end - start) / st.fps, 4),
        'loop': loop, 'seam': round(seam, 6), 'bounce': round(max(heights) - min(heights), 5) if heights else 0.0,
        'stride': round(stride, 4), 'foot_slide': round(slide, 5), 'contact_frames': contact,
        'ground_speed': round(speed, 4), 'rigify': st.rigify,
    }

def _j_report(st, action, **extra):
    row = _j_measure(st, action)
    row.update(extra)
    print('JOINERY_ANIMATION:' + _joinery_json.dumps(row, sort_keys=True))

def _joinery_make_cycle(armature, kind, params):
    rig = _j_armature(armature)
    motions = _J_KINDS[kind]
    need = _J_NEEDS['arms'] if kind in ('wave', 'attack', 'swing') else ()
    need = need + _J_NEEDS['legs']
    st = _JRig(rig, need)
    names = [params['name']] if 'name' in params else list(motions)
    for motion, name in zip(motions, names):
        action = _j_make(st, motion, name, params)
        _j_report(st, action, op='make_cycle')

def _joinery_edit_action(armature, name, ops):
    action = bpy.data.actions.get(name)
    if action is None:
        names = ', '.join(a.name for a in bpy.data.actions) or 'none'
        raise RuntimeError(f'There is no action named {name!r} in this model. Actions: {names}')
    rig = _j_armature(armature or _j_owner(action))
    st = _JRig(rig)
    done, results = [], {}
    for op in ops:
        if op['op'] == 'delete':
            _j_unstash(rig, action)
            bpy.data.actions.remove(action)
            print('JOINERY_ANIMATION:' + _joinery_json.dumps(
                {'action': name, 'was': name, 'deleted': True, 'op': 'delete', 'armature': rig.name}))
            return
        if op['op'] == 'copy':
            copy = bpy.data.actions[_J_EDITS['copy'](st, action, op)['copy']]
            _j_report(st, copy, op='copy', was='', copied_from=action.name)
            continue
        results.update(_J_EDITS[op['op']](st, action, op))
        done.append(op['op'])
    if done:
        _j_report(st, action, op='edit_action', ops=done, was=name, **results)

def _j_owner(action):
    for obj in bpy.data.objects:
        data = obj.animation_data
        if obj.type != 'ARMATURE' or data is None:
            continue
        if data.action == action or any(s.action == action for t in data.nla_tracks for s in t.strips):
            return obj.name
    return ''

def _joinery_measure(armature, names):
    for name in names:
        action = bpy.data.actions.get(name)
        if action is None:
            raise RuntimeError(f'There is no action named {name!r}.')
        rig = _j_armature(armature or _j_owner(action))
        _j_report(_JRig(rig), action, op='measure')
'''
