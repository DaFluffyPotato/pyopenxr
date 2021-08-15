from abc import ABC, abstractmethod
import enum
import re
from typing import Optional

import clang.cindex
from clang.cindex import TypeKind


class Api(enum.Enum):
    C = 1,  # C language symbols and code from the original C file
    CTYPES = 2,  # Python code with maximum similarity to C code
    PYTHON = 3,  # High-level pythonic interface symbols and code


class TypeBase(ABC):
    def __init__(self, clang_type: clang.cindex.Type):
        self.clang_type = clang_type

    @abstractmethod
    def name(self, api=Api.PYTHON) -> str:
        pass

    @abstractmethod
    def used_ctypes(self, api=Api.PYTHON) -> set[str]:
        pass


class ArrayType(TypeBase):
    def __init__(self, clang_type: clang.cindex.Type):
        super().__init__(clang_type)
        assert clang_type.kind == TypeKind.CONSTANTARRAY
        self.element_type = parse_type(clang_type.element_type)
        self.count = clang_type.element_count

    def name(self, api=Api.PYTHON) -> str:
        if api == Api.C:
            return f"{self.element_type.name(api)}[{self.count}]"
        else:
            return f"({self.element_type.name(api)} * {self.count})"

    def used_ctypes(self, api=Api.PYTHON) -> set[str]:
        return self.element_type.used_ctypes(api)


class EnumType(TypeBase):
    def __init__(self, clang_type: clang.cindex.Type):
        super().__init__(clang_type)
        assert clang_type.kind == TypeKind.ENUM

    def name(self, api=Api.PYTHON) -> str:
        if api == Api.C:
            return self.clang_type.spelling
        elif api == Api.CTYPES:
            return "c_int"
        else:
            return "c_int"  # TODO we could use the actual name if we had the enums loaded

    def used_ctypes(self, api=Api.PYTHON) -> set[str]:
        return {
            "c_int",
        }


class FunctionPointerType(TypeBase):
    def __init__(self, clang_type: clang.cindex.Type):
        super().__init__(clang_type)
        assert clang_type.kind == TypeKind.POINTER
        pt = clang_type.get_pointee()
        assert pt.kind == TypeKind.FUNCTIONPROTO
        self.result_type = parse_type(pt.get_result())
        self.arg_types = [parse_type(t) for t in pt.argument_types()]

    def name(self, api=Api.PYTHON) -> str:
        if api == api.C:
            return self.clang_type.spelling
        else:
            arg_string = ", ".join(
                a.name(Api.CTYPES) for a in [self.result_type, *self.arg_types]
            )
            return f"CFUNCTYPE({arg_string})"

    def used_ctypes(self, api=Api.PYTHON) -> set[str]:
        result = {
            "CFUNCTYPE",
        }
        result.update(self.result_type.used_ctypes(api))
        result.update(self.result_type.used_ctypes(api))
        for a in self.arg_types:
            result.update(a.used_ctypes(api))
        return result


class IntegerType(TypeBase):
    def __init__(self, clang_type: clang.cindex.Type):
        self._name = IntegerType.clang_name_for_type(clang_type)
        if self._name is None:
            raise ValueError(f"clang type `{clang_type.kind}` is not an integer")
        super().__init__(clang_type)

    CLANG_NAMES_FOR_KINDS = {
        TypeKind.INT: "c_int",
        TypeKind.LONG: "c_long",
        TypeKind.LONGLONG: "c_longlong",
        TypeKind.SHORT: "c_short",
        TypeKind.UINT: "c_uint",
        TypeKind.ULONG: "c_ulong",
        TypeKind.ULONGLONG: "c_ulonglong",
        TypeKind.USHORT: "c_ushort",
    }

    @staticmethod
    def clang_name_for_type(clang_type: clang.cindex.Type) -> Optional[str]:
        if clang_type.kind in IntegerType.CLANG_NAMES_FOR_KINDS:
            return IntegerType.CLANG_NAMES_FOR_KINDS[clang_type.kind]
        if clang_type.kind == TypeKind.TYPEDEF:
            return IntegerType.clang_name_for_c_name(clang_type.spelling)

    @staticmethod
    def clang_name_for_c_name(c_name: str) -> Optional[str]:
        m = re.match(r"(?:const )?(u?int(?:8|16|32|64))_t", c_name)
        if m:
            return f"c_{m.group(1)}"
        return None

    def name(self, api=Api.PYTHON) -> str:
        if api == Api.C:
            return self.clang_type.spelling
        elif api == Api.PYTHON:
            return "int"
        else:
            return self._name

    def used_ctypes(self, api=Api.PYTHON) -> set[str]:
        if api == Api.CTYPES:
            return {self._name, }
        else:
            return set()


class PointerType(TypeBase):
    def __init__(self, clang_type: clang.cindex.Type):
        super().__init__(clang_type)
        assert clang_type.kind == TypeKind.POINTER
        pt = clang_type.get_pointee()
        assert pt.kind != TypeKind.VOID
        assert pt.kind != TypeKind.FUNCTIONPROTO
        self.pointee = parse_type(pt)

    def name(self, api=Api.PYTHON) -> str:
        if api == Api.C:
            return self.clang_type.spelling
        else:
            return f"POINTER({self.pointee.name(api)})"

    def used_ctypes(self, api=Api.PYTHON) -> set[str]:
        result = self.pointee.used_ctypes(api)
        result.add("POINTER")
        return result


class PrimitiveCTypesType(TypeBase):
    def __init__(self, clang_type: clang.cindex.Type, ctypes_type: str, python_type: str):
        super().__init__(clang_type)
        self._name = ctypes_type
        self.py_name = python_type

    def name(self, api=Api.PYTHON) -> str:
        if api == Api.C:
            return self.clang_type.spelling
        elif api == Api.PYTHON:
            return self.py_name
        else:
            return self._name

    def used_ctypes(self, api=Api.PYTHON) -> set[str]:
        return {
            self._name,
        }


class RecordType(TypeBase):
    def __init__(self, clang_type: clang.cindex.Type):
        super().__init__(clang_type)
        assert clang_type.kind == TypeKind.RECORD
        self._capi_name = capi_type_name(clang_type.get_declaration().spelling)
        self._py_name = py_type_name(self._capi_name)

    def name(self, api=Api.PYTHON) -> str:
        if api == Api.C:
            return self.clang_type.spelling
        elif api == Api.CTYPES:
            return self._py_name
        elif api == Api.PYTHON:
            return self._py_name
        else:
            raise NotImplementedError

    def used_ctypes(self, api=Api.PYTHON) -> set[str]:
        return set()


class TypedefType(TypeBase):
    def __init__(self, clang_type: clang.cindex.Type):
        super().__init__(clang_type)
        assert clang_type.kind == TypeKind.TYPEDEF
        type_name = clang_type.spelling
        self._capi_name = capi_type_name(type_name)
        self._py_name = py_type_name(self._capi_name)
        self.underlying_type = parse_type(
            clang_type.get_declaration().underlying_typedef_type
        )

    def name(self, api=Api.PYTHON) -> str:
        if api == Api.C:
            return self.clang_type.spelling
        elif api == Api.CTYPES:
            return self._py_name
        elif api == Api.PYTHON:
            return self._py_name
        else:
            raise NotImplementedError

    def used_ctypes(self, api=Api.PYTHON) -> set[str]:
        if api == Api.C:
            return set()
        elif self._capi_name.startswith("c_"):
            return {
                self._capi_name,
            }
        else:
            return set()


class VoidType(TypeBase):
    def __init__(self, clang_type: clang.cindex.Type):
        super().__init__(clang_type)
        assert clang_type.kind == TypeKind.VOID

    def name(self, api=Api.PYTHON) -> str:
        if api == Api.C:
            return "void"
        else:
            return "None"

    def used_ctypes(self, api=Api.PYTHON) -> set[str]:
        return set()


def capi_type_name(c_type_name: str) -> str:
    """The low level C-like api uses the exact same names as in C"""
    s = re.sub(r"\b(?:const|volatile)\s+", "", c_type_name)  # But without const
    return s


def parse_type(clang_type: clang.cindex.Type) -> TypeBase:
    if clang_type.kind == TypeKind.CHAR_S:
        return PrimitiveCTypesType(clang_type, "c_char", "str")
    elif clang_type.kind == TypeKind.CONSTANTARRAY:
        return ArrayType(clang_type)
    elif clang_type.kind == TypeKind.ELABORATED:
        return parse_type(clang_type.get_named_type())
    elif clang_type.kind == TypeKind.ENUM:
        return EnumType(clang_type)
    elif clang_type.kind == TypeKind.FLOAT:
        return PrimitiveCTypesType(clang_type, "c_float", "float")
    elif clang_type.kind in IntegerType.CLANG_NAMES_FOR_KINDS:
        return IntegerType(clang_type)
    elif clang_type.kind == TypeKind.POINTER:
        pt = clang_type.get_pointee()
        if pt.kind == TypeKind.CHAR_S:
            # But this works ONLY if these are always null terminated strings
            return PrimitiveCTypesType(clang_type, "c_char_p", "str")
        elif pt.kind == TypeKind.FUNCTIONPROTO:
            return FunctionPointerType(clang_type)
        elif pt.kind == TypeKind.VOID:
            return PrimitiveCTypesType(clang_type, "c_void_p", "None")
        else:
            return PointerType(clang_type)
    elif clang_type.kind == TypeKind.RECORD:
        return RecordType(clang_type)
    elif clang_type.kind == TypeKind.TYPEDEF:
        try:
            return IntegerType(clang_type)
        except ValueError:
            return TypedefType(clang_type)
    elif clang_type.kind == TypeKind.UCHAR:
        return PrimitiveCTypesType(clang_type, "c_uchar", "str")
    elif clang_type.kind == TypeKind.VOID:
        return VoidType(clang_type)

    assert False


def py_type_name(capi_type: str) -> str:
    s = capi_type
    if s.startswith("Xr"):
        s = s[2:]
    return s


__all__ = [
    "Api",
    "ArrayType",
    "capi_type_name",
    "EnumType",
    "FunctionPointerType",
    "PointerType",
    "parse_type",
    "PrimitiveCTypesType",
    "py_type_name",
    "RecordType",
    "TypeBase",
    "TypedefType",
    "VoidType",
]
