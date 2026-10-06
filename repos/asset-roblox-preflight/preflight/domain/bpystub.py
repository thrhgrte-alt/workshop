"""A tiny fake of the parts of Blender's `bpy` that the generated fix scripts use.

This is NOT Blender. It exists so tests and evals can execute a generated script against a fake scene and see which renames and transform
applications it performs. It proves the script runs and does what it says on this stub; it does not prove anything about real Blender.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import types


class FakeData:
    def __init__(self, users: int = 1):
        self.users = users

    def copy(self):
        return FakeData(1)


class FakeObject:
    def __init__(self, name: str, type_: str, scale=(1.0, 1.0, 1.0), rotation=(0.0, 0.0, 0.0), users: int = 1):
        self.name, self.type, self.scale, self.rotation = name, type_, tuple(scale), tuple(rotation)
        self.data = FakeData(users) if type_ == "MESH" else None
        self.selected = False

    def select_set(self, flag: bool):
        self.selected = flag


class FakeCollection(dict):
    """dict by name with Blender-like .get and `in`."""


class FakeBone:
    def __init__(self, name: str):
        self.name = name


class FakeBones:
    def __init__(self, names):
        self._b = {n: FakeBone(n) for n in names}

    def get(self, name):
        return self._b.get(name)

    def __contains__(self, name):
        return name in self._b

    def rename(self, old, new):
        self._b[new] = self._b.pop(old)


class _BoneProxy:
    """Assigning bone.name renames it inside its collection, like Blender."""


class FakeArmature:
    def __init__(self, names):
        self.bones = _RenamingBones(names)


class _RenamingBones(FakeBones):
    def __init__(self, names):
        super().__init__(names)
        for n, b in self._b.items():
            self._wrap(b)

    def _wrap(self, bone):
        owner = self
        cls = type("RenamingBone", (FakeBone,), {})
        old_init_name = bone.name

        def setter(self_b, value, owner=owner):
            current = self_b.__dict__["_name"]
            owner._b[value] = owner._b.pop(current)
            self_b.__dict__["_name"] = value

        cls.name = property(lambda s: s.__dict__["_name"], setter)
        bone.__class__ = cls
        bone.__dict__["_name"] = old_init_name
        bone.__dict__.pop("name", None)


class _Objects:
    """Collection with live renaming: obj.name = 'x' updates the key."""

    def __init__(self):
        self._o: dict[str, FakeObject] = {}

    def add(self, obj: FakeObject):
        cls = type("RenamingObject", (FakeObject,), {})
        owner = self

        def setter(self_o, value, owner=owner):
            cur = self_o.__dict__["_name"]
            owner._o[value] = owner._o.pop(cur)
            self_o.__dict__["_name"] = value

        cls.name = property(lambda s: s.__dict__["_name"], setter)
        name = obj.name
        obj.__class__ = cls
        obj.__dict__["_name"] = name
        obj.__dict__.pop("name", None)
        self._o[name] = obj

    def get(self, name):
        return self._o.get(name)

    def __contains__(self, name):
        return name in self._o

    def __iter__(self):
        return iter(list(self._o.values()))

    def names(self):
        return sorted(self._o)


def make_bpy(objects: list[tuple], armatures: list[list[str]] | None = None):
    """objects: (name, type, scale, rotation). Returns (module, log)."""
    log: list[str] = []
    coll = _Objects()
    for o in objects:
        coll.add(FakeObject(*o))
    view = types.SimpleNamespace(objects=coll)
    view.objects.active = None
    arms = [FakeArmature(n) for n in (armatures or [])]
    mod = types.ModuleType("bpy")
    mod.data = types.SimpleNamespace(objects=coll, armatures=arms)
    mod.context = types.SimpleNamespace(view_layer=types.SimpleNamespace(objects=_ActiveObjects(coll)))

    def transform_apply(location=False, rotation=False, scale=False):
        obj = mod.context.view_layer.objects.active
        log.append(f"transform_apply({obj.name}, location={location}, rotation={rotation}, scale={scale})")
        if scale:
            obj.scale = (1.0, 1.0, 1.0)
        if rotation:
            obj.rotation = (0.0, 0.0, 0.0)

    mod.ops = types.SimpleNamespace(object=types.SimpleNamespace(transform_apply=transform_apply))
    return mod, log, coll, arms


class _ActiveObjects:
    def __init__(self, coll):
        self._c = coll
        self.active = None

    def __iter__(self):
        return iter(self._c)


def run_script(script: str, objects: list[tuple], armatures: list[list[str]] | None = None) -> dict:
    """Execute a generated fix script against a fake scene. Returns the script's own result, the call log and the final names."""
    mod, log, coll, arms = make_bpy(objects, armatures)
    saved = sys.modules.get("bpy")
    sys.modules["bpy"] = mod
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            exec(compile(script, "<generated fix script>", "exec"), {"__name__": "__main__"})
    finally:
        if saved is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = saved
    line = next((ln for ln in out.getvalue().splitlines() if ln.startswith("PREFLIGHT_FIX_RESULT ")), None)
    return {"result": json.loads(line.split(" ", 1)[1]) if line else None, "calls": log, "object_names": coll.names(),
            "bone_names": sorted(n for a in arms for n in a.bones._b), "scales": {o.name: list(o.scale) for o in coll}, "rotations": {o.name: list(o.rotation) for o in coll}}
