"""Run one question's check code in a locked-down child process.

The code comes from the question setter (Claude) and must compute the answer from the
givens and store it in `result` (or print it, for "what does this program print"
questions). Reads code on stdin, prints one JSON line: {"ok": true, "result": ...}.

Defence in depth (the parent also runs this with `python -I`, a timeout and a CPU limit):
only plain computation syntax, a short list of maths modules, no dunder names or
attributes, no file, network, eval or exec builtins.
"""
import ast
import io
import json
import sys

ALLOWED_MODULES = {"math", "cmath", "fractions", "itertools", "functools", "statistics",
                   "decimal", "collections", "string", "operator", "heapq", "bisect"}
BANNED_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars",
                "getattr", "setattr", "delattr", "input", "breakpoint", "help", "exit", "quit",
                "memoryview", "type", "object", "super", "classmethod", "staticmethod", "property",
                "dir", "id", "__builtins__", "__loader__", "__spec__"}
SAFE_BUILTINS = ["abs", "all", "any", "bin", "bool", "chr", "complex", "dict", "divmod", "enumerate",
                 "filter", "float", "format", "frozenset", "hash", "hex", "int", "isinstance", "iter",
                 "len", "list", "map", "max", "min", "next", "oct", "ord", "pow", "print", "range",
                 "repr", "reversed", "round", "set", "slice", "sorted", "str", "sum", "tuple", "zip",
                 "ValueError", "ZeroDivisionError", "StopIteration", "IndexError", "KeyError",
                 "ArithmeticError", "Exception", "AssertionError", "TypeError", "RecursionError"]


class Unsafe(Exception):
    pass


def check_ast(code):
    tree = ast.parse(code, mode="exec")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for n in names:
                if n.split(".")[0] not in ALLOWED_MODULES:
                    raise Unsafe("import of %s is not allowed" % n)
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise Unsafe("attribute %s is not allowed" % node.attr)
        elif isinstance(node, ast.Name):
            if node.id in BANNED_NAMES or node.id.startswith("__"):
                raise Unsafe("name %s is not allowed" % node.id)
        elif isinstance(node, (ast.AsyncFunctionDef, ast.Await, ast.AsyncFor, ast.AsyncWith)):
            raise Unsafe("async code is not allowed")
    return tree


def jsonable(x, depth=0):
    from fractions import Fraction
    from decimal import Decimal
    if depth > 4:
        return repr(x)
    if isinstance(x, bool) or x is None or isinstance(x, str):
        return x
    if isinstance(x, int):
        return x if abs(x) < 2 ** 53 else float(x)
    if isinstance(x, float):
        return x
    if isinstance(x, (Fraction, Decimal)):
        return float(x)
    if isinstance(x, complex):
        return repr(x)
    if isinstance(x, (list, tuple)):
        return [jsonable(v, depth + 1) for v in x][:50]
    return repr(x)[:500]


def main():
    code = sys.stdin.read()
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (6, 6))
    except Exception:
        pass
    try:
        tree = check_ast(code)
    except SyntaxError as e:
        print(json.dumps({"ok": False, "error": "SyntaxError: %s" % e}))
        return
    except Unsafe as e:
        print(json.dumps({"ok": False, "error": "unsafe: %s" % e}))
        return
    import builtins
    real_import = builtins.__import__

    def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level != 0 or name.split(".")[0] not in ALLOWED_MODULES:
            raise ImportError("import of %s is not allowed" % name)
        return real_import(name, globals, locals, fromlist, level)

    safe = {n: getattr(builtins, n) for n in SAFE_BUILTINS}
    safe["__import__"] = safe_import
    safe["__build_class__"] = builtins.__build_class__  # lets a program question define a small class
    env = {"__builtins__": safe, "__name__": "check"}
    out = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = out
    try:
        exec(compile(tree, "<check>", "exec"), env)
        err = None
    except BaseException as e:  # noqa: BLE001  (report any failure as data)
        err = "%s: %s" % (type(e).__name__, str(e)[:300])
    finally:
        sys.stdout = real_stdout
    if err:
        print(json.dumps({"ok": False, "error": err}))
        return
    printed = out.getvalue().strip()
    if "result" in env:
        print(json.dumps({"ok": True, "result": jsonable(env["result"]), "printed": printed[:2000]}))
    elif printed:
        print(json.dumps({"ok": True, "result": printed[:2000], "printed": printed[:2000]}))
    else:
        print(json.dumps({"ok": False, "error": "the check code set no `result` and printed nothing"}))


if __name__ == "__main__":
    main()
