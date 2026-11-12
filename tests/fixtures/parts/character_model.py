# The character fixture's operation code (see build_character.py): an MPFB2
# human with MPFB2's Rigify meta-rig and weights, the rig generated, and two
# keys the game animates, moved on the mouth only.
from bl_ext.system.mpfb.services.humanservice import HumanService
from bl_ext.system.mpfb.services.rigservice import RigService

human = HumanService.create_human()
human.name = "Hero"
human.data.name = "Hero"
meta = HumanService.add_builtin_rig(human, "rigify.human_toes", import_weights=True)
rig = RigService.generate_rigify_rig(meta, name="HeroRig", meta_rig_action="delete")
assert rig is not None, "Rigify rejected the MPFB2 meta-rig"
rig.name = "HeroRig"
mouth_bones = ("DEF-jaw", "DEF-chin", "DEF-lip.B.L", "DEF-lip.B.R")
mouth_groups = {g.index for g in human.vertex_groups if g.name in mouth_bones}
mouth = [
    v.index
    for v in human.data.vertices
    if any(e.group in mouth_groups and e.weight > 0.5 for e in v.groups)
]
for name, lift in (("smile", 0.004), ("viseme_aa", -0.006)):
    key = human.shape_key_add(name=name, from_mix=False)
    for index in mouth:
        key.data[index].co.z += lift
