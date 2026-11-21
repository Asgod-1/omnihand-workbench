"""Feedback-gated clearance poses for the right O10 hand."""

# Zero-based axes. Larger values extend these flexion axes, as in SDK PAPER.
THUMB_FLEX = 2
FINGER_FLEX = (4, 5, 7, 9)


def gesture_stages(start, target, open_pose, tolerance):
    """Keep the fingers extended while repositioning thumb rotation and spread."""
    if max(abs(a - b) for a, b in zip(start, target)) <= tolerance:
        return [("进入目标姿态", target)]

    thumb_open = list(start)
    thumb_open[THUMB_FLEX] = max(start[THUMB_FLEX], open_pose[THUMB_FLEX])
    fingers_open = list(thumb_open)
    for axis in FINGER_FLEX:
        fingers_open[axis] = max(start[axis], open_pose[axis])

    aligned = list(target)
    for axis in (THUMB_FLEX, *FINGER_FLEX):
        aligned[axis] = fingers_open[axis]
    fingers_closed = list(target)
    fingers_closed[THUMB_FLEX] = aligned[THUMB_FLEX]

    stages = []
    previous = start
    for name, positions in (
        ("伸直拇指", thumb_open),
        ("张开四指", fingers_open),
        ("调整拇指方向和侧摆", aligned),
        ("四指进入目标", fingers_closed),
    ):
        if max(abs(a - b) for a, b in zip(previous, positions)) > tolerance:
            stages.append((name, positions))
            previous = positions

    if stages and stages[-1][1] == target:
        stages[-1] = ("进入目标姿态", target)
    else:
        stages.append(("进入目标姿态", target))
    return stages
