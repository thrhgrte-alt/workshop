"""A minimal stand-in for Designer's Python API, driven by the node catalog.

It lets tests EXECUTE the generated scripts end to end. It mirrors the API *shape the scripts assume*
(sd.getContext().getSDApplication(), SDGraph.newNode, newPropertyConnectionFromId, SDValue*.sNew ...).
It cannot prove those calls exist in real Designer - only the probe script run inside Designer can.
"""

from __future__ import annotations

import sys
import types
from contextlib import contextmanager


class SDTypeFloat: ...
class SDTypeInt: ...
class SDTypeBool: ...
class SDTypeColorRGBA: ...
class SDTypeEnum: ...
class SDTypeTexture: ...


class Cat:
    Input, Output, Annotation = "Input", "Output", "Annotation"


class Val:
    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v


def _val_cls(name):
    cls = type(name, (), {})
    cls.sNew = staticmethod(lambda *a: Val(a[-1]))
    return cls


class Prop:
    def __init__(self, pid, type_):
        self.pid, self.type_ = pid, type_

    def getId(self):
        return self.pid

    def getType(self):
        return self.type_


class Node:
    reject_connections = False  # flipped on by install(fail_connections=True)
    registry: dict = {}  # ident -> node, so connections can resolve their target

    def __init__(self, ident, definition, inputs, params, outputs):
        self.ident, self.definition = ident, definition
        self.inputs = {p: Prop(p, SDTypeTexture()) for p in inputs}
        self.params = {p: Prop(p, t()) for p, t in params.items()}
        self.outputs = {p: Prop(p, SDTypeTexture()) for p in outputs}
        self.values, self.links, self.annotations, self.pos = {}, [], {}, None
        Node.registry[ident] = self

    def getIdentifier(self):
        return self.ident

    def getDefinition(self):
        return types.SimpleNamespace(getId=lambda: self.definition)

    def getPropertyValue(self, prop):
        return Val(self.values.get(prop.getId()))

    def getPropertyConnections(self, prop):
        out = []
        for out_id, dst_ident, in_id in self.links:
            if out_id == prop.getId():
                dst = Node.registry[dst_ident]
                out.append(types.SimpleNamespace(getInputPropertyNode=lambda d=dst: d,
                                                 getInputProperty=lambda i=in_id: types.SimpleNamespace(getId=lambda: i)))
        return out

    def setPosition(self, pos):
        self.pos = pos

    def getPropertyFromId(self, pid, cat):
        return {**self.inputs, **self.params}.get(pid) if cat == Cat.Input else self.outputs.get(pid)

    def setInputPropertyValueFromId(self, pid, value):
        assert pid in self.params, f"{pid} is not a parameter of {self.definition}"
        self.values[pid] = value.get()

    def setAnnotationPropertyValueFromId(self, pid, value):
        self.annotations[pid] = value.get()

    def getProperties(self, cat):
        if cat == Cat.Input:
            return list(self.inputs.values()) + list(self.params.values())
        return list(self.outputs.values()) if cat == Cat.Output else []

    def newPropertyConnectionFromId(self, out_id, dst, in_id):
        if Node.reject_connections or out_id not in self.outputs or in_id not in {**dst.inputs, "inputNodeOutput": None}:
            return None
        if self.ident == dst.ident:
            return None
        self.links.append((out_id, dst.ident, in_id))
        return object()


class Graph:
    def __init__(self, library, atomic):
        self.library, self.atomic, self.nodes, self.ident = library, atomic, [], None

    def setIdentifier(self, name):
        self.ident = name

    def getIdentifier(self):
        return self.ident

    def getNodes(self):
        return list(self.nodes)

    def _make(self, definition, spec):
        n = Node(f"node_{len(Node.registry)}", definition, *spec)
        self.nodes.append(n)
        return n

    def newNode(self, definition):
        if definition == "sbs::compositing::output":
            return self._make(definition, (["inputNodeOutput"], {}, []))
        if definition not in self.atomic:
            raise RuntimeError(f"unknown atomic node {definition}")
        return self._make(definition, self.atomic[definition])

    def newInstanceNode(self, resource):
        return self._make(resource.name, resource.spec)


class Resource:
    def __init__(self, name, spec):
        self.name, self.spec = name, spec

    def getIdentifier(self):
        return self.name


class Package:
    def __init__(self, resources):
        self.resources = resources

    def getChildrenResources(self, recursive):
        return self.resources


class PkgMgr:
    def __init__(self, library_resources):
        self.library = Package(library_resources)
        self.created, self.saved = [], []

    def getPackages(self):
        return [self.library]

    def newUserPackage(self):
        self.created.append(object())
        return self.created[-1]

    def savePackageAs(self, package, path):
        self.saved.append(path)


def _spec(node):
    types_ = {"float": SDTypeFloat, "int": SDTypeInt, "bool": SDTypeBool, "color": SDTypeColorRGBA, "enum": SDTypeEnum}
    return ([s.id for s in node.inputs], {p.id: types_[p.type] for p in node.params.values()}, [s.id for s in node.outputs])


@contextmanager
def install(catalog, missing_library: tuple = (), fail_connections: bool = False):
    """Put fake ``sd`` modules into sys.modules for the duration of the block. Yields the graph registry."""
    atomic = {n.definition: _spec(n) for n in catalog.values() if n.source == "atomic"}
    resources = [Resource(n.label.lower().replace(" ", "_"), _spec(n)) for n in catalog.values()
                 if n.source == "library" and n.label not in missing_library]
    mgr = PkgMgr(resources)
    graphs: list[Graph] = []

    class App:
        def getPackageMgr(self):
            return mgr

        def getVersion(self):
            return "fake-1.0"

        def getQtForPythonUIMgr(self):
            return types.SimpleNamespace(getCurrentGraph=lambda: graphs[-1] if graphs else None)

    class Ctx:
        def getSDApplication(self):
            return App()

    class CompGraph:
        @staticmethod
        def sNew(package):
            g = Graph(resources, atomic)
            graphs.append(g)
            return g

    mods = {
        "sd": types.SimpleNamespace(getContext=lambda: Ctx()),
        "sd.api": types.ModuleType("sd.api"),
        "sd.api.sdbasetypes": types.SimpleNamespace(float2=lambda x, y: (x, y),
                                                    ColorRGBA=lambda r, g, b, a: (r, g, b, a)),
        "sd.api.sdproperty": types.SimpleNamespace(SDPropertyCategory=Cat),
        "sd.api.sdvaluefloat": types.SimpleNamespace(SDValueFloat=_val_cls("SDValueFloat")),
        "sd.api.sdvalueint": types.SimpleNamespace(SDValueInt=_val_cls("SDValueInt")),
        "sd.api.sdvaluebool": types.SimpleNamespace(SDValueBool=_val_cls("SDValueBool")),
        "sd.api.sdvaluecolorrgba": types.SimpleNamespace(SDValueColorRGBA=_val_cls("SDValueColorRGBA")),
        "sd.api.sdvalueenum": types.SimpleNamespace(SDValueEnum=_val_cls("SDValueEnum")),
        "sd.api.sdvaluestring": types.SimpleNamespace(SDValueString=_val_cls("SDValueString")),
        "sd.api.sbs": types.ModuleType("sd.api.sbs"),
        "sd.api.sbs.sdsbscompgraph": types.SimpleNamespace(SDSBSCompGraph=CompGraph),
    }
    saved = {k: sys.modules.get(k) for k in mods}
    Node.registry.clear()
    Node.reject_connections = fail_connections
    sys.modules.update(mods)
    try:
        yield types.SimpleNamespace(graphs=graphs, manager=mgr)
    finally:
        Node.reject_connections = False
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def run_script(source: str) -> None:
    exec(compile(source, "<generated>", "exec"), {"__name__": "__designer_script__"})
