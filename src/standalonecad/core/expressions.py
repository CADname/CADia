from __future__ import annotations
import ast
import math
import operator
import re
from typing import Mapping

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
       ast.Pow: operator.pow, ast.Mod: operator.mod}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_CONST = {"pi": math.pi, "e": math.e}
_FUNCS = {"sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
          "asin": math.asin, "acos": math.acos, "atan": math.atan, "abs": abs,
          "min": min, "max": max}

# Additive unit support.  Internal command-layer bases remain mm/degrees.
_LENGTH_TO_MM = {"mm":1.0,"cm":10.0,"m":1000.0,"in":25.4,"inch":25.4,"inches":25.4,"ft":304.8,"foot":304.8,"feet":304.8}
_ANGLE_TO_DEG = {"deg":1.0,"degree":1.0,"degrees":1.0,"rad":180.0/math.pi,"radian":180.0/math.pi,"radians":180.0/math.pi}
_UNIT_TOKEN = re.compile(r'(?<![A-Za-z0-9_\.])((?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(mm|cm|m|inches|inch|in|feet|foot|ft|degrees|degree|deg|radians|radian|rad)\b', re.I)


def _eval_ast(text: str, parameters: Mapping[str, float], funcs=None) -> float:
    tree = ast.parse(text, mode="eval"); funcs=_FUNCS if funcs is None else funcs
    def ev(n):
        if isinstance(n, ast.Expression): return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int,float)): return float(n.value)
        if isinstance(n, ast.Name):
            if n.id in parameters: return float(parameters[n.id])
            if n.id in _CONST: return _CONST[n.id]
            raise ValueError(f"Unknown parameter: {n.id}")
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN: return _BIN[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _UNARY: return _UNARY[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in funcs:
            return float(funcs[n.func.id](*[ev(a) for a in n.args]))
        raise ValueError("Unsupported expression")
    return float(ev(tree))


def _legacy_eval(value, parameters: Mapping[str, float]) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        raise ValueError(f"Expected number/expression, got {type(value).__name__}")
    text = value.strip().replace("^", "**")
    # Preserve the established behavior first: keep every already-successful expression.
    for suffix in (" mm", "mm", " deg", "deg"):
        if text.lower().endswith(suffix):
            text = text[:-len(suffix)].strip()
            break
    return _eval_ast(text, parameters)


def _extended_unit_eval(value: str, parameters: Mapping[str, float]) -> float:
    text=value.strip().replace("^","**")
    found=[]
    def repl(m):
        number=float(m.group(1)); unit=m.group(2).lower()
        if unit in _LENGTH_TO_MM:
            found.append("length"); factor=_LENGTH_TO_MM[unit]
        else:
            found.append("angle"); factor=_ANGLE_TO_DEG[unit]
        return f"({number!r}*{factor!r})"
    converted=_UNIT_TOKEN.sub(repl,text)
    if converted==text:
        raise ValueError("Unsupported expression")
    # Reject physically ambiguous mixed length/angle literals rather than guessing.
    if len(set(found))>1:
        raise ValueError("Cannot mix length and angle unit literals in one expression")
    funcs=_FUNCS
    if found and set(found)=={'angle'}:
        funcs=dict(_FUNCS); funcs.update({'sin':lambda x:math.sin(math.radians(x)),'cos':lambda x:math.cos(math.radians(x)),'tan':lambda x:math.tan(math.radians(x)),'asin':lambda x:math.degrees(math.asin(x)),'acos':lambda x:math.degrees(math.acos(x)),'atan':lambda x:math.degrees(math.atan(x))})
    return _eval_ast(converted,parameters,funcs)


def eval_expr(value, parameters: Mapping[str, float]) -> float:
    """Evaluate an expression with a strict legacy-first monotonic policy.

    The established expression semantics are attempted unchanged.  Only expressions that the legacy
    evaluator rejects are offered to the extended explicit-unit parser, so no prior
    successful expression changes meaning while cm/m/in/ft/rad literals become usable.
    """
    try:
        return _legacy_eval(value,parameters)
    except (ValueError, SyntaxError):
        if isinstance(value,str):
            return _extended_unit_eval(value,parameters)
        raise
