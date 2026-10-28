"""#165: the packet's output contract gives every field's type, nested objects included, so an agent doesn't hand a list
where a string belongs.

Water, N21 a7: `defects[].feedback` was submitted as a list, and the schema needs a string. The contract listed the field
names only.
"""
from __future__ import annotations

import json

from tests.conftest import PKG
from tests.test_hardening import _init_film
from vpg.engine import Engine
from vpg.executors import TaskPacketExecutor, _resolver, _type_label, contract_block, output_contracts

SCHEMAS = PKG / "schemas" / "artifacts"


def _block(name):
    path = SCHEMAS / f"{name}.schema.json"
    return contract_block(name, path, json.loads(path.read_text()))


def test_the_interpretation_contract_types_every_field(tmp_path):
    eng = Engine(_init_film(tmp_path), executor=TaskPacketExecutor())
    node = next(n for n in eng.wf.nodes if any(o.schema_name == "interpretation" for o in n.outputs))
    text = output_contracts(eng._context(node, 1, "fp", "tok"))
    assert ('`defects` item types: dimension string; severity one of "P1"|"P2"|"P3"|"nit"; description string; '
            "evidence string; traced_node string; smallest_fix string; feedback string") in text
    assert "`feedback` item types: id string; defect_ids array of string; matches object; no_action_reason string" in text
    assert "types: defects array of object; feedback array of object; fixes map of string; resolved array of (string or object)" in text
    assert "each `resolved` item, when an object: id* string; evidence string (* required)" in text


def test_the_task_packet_carries_the_types(tmp_path):
    proj = _init_film(tmp_path)
    eng = Engine(proj, executor=TaskPacketExecutor())
    eng.run(max_steps=5)
    text = (proj / eng.rt("n01_context_intake").task_packet).read_text()
    assert "each `open_questions` item: id*, text*, blocking, owner, status, answer (* required; no other keys)" in text
    assert "`open_questions` item types: id string; text string;" in text


def test_nested_objects_are_typed_by_path():
    assert ("each `chapters[].units` item: id* string; text* string; claims array of string; terms array of string "
            "(* required)") in _block("script")
    assert '`beats[].source` fields: type* one of "captured"|"supplied"|"generated"|"rendered"; ref string (* required)' \
        in _block("storyboard")
    inventory = _block("inventory")
    assert "chapters array of array of [string, number, number]" in inventory
    assert ("`samples[]` element 2: texts* array of string; cap* string or null; zoom* number or null; pointer* boolean; "
            "ring* boolean (* required)") in inventory


def test_every_packaged_schema_renders():
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        text = contract_block(path.stem, path, json.loads(path.read_text()))
        assert text.startswith(f"- `{path.stem}` → schema") and "\n  - types: " in text   # `any` only for an empty schema


def test_type_labels_group_quote_and_stop():
    resolve = _resolver({"definitions": {"sev": {"enum": ["P1", "1", 1, None, "null"]}}})
    assert _type_label({"$ref": "#/definitions/sev"}, resolve) == 'one of "P1"|"1"|1|null|"null"'
    assert _type_label({"type": ["string", "null"]}, resolve) == "string or null"
    assert _type_label({"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]}, resolve) == \
        "array of string or null"
    assert _type_label({"type": "array", "items": {"anyOf": [{"type": "string"}, {"type": "null"}]}}, resolve) == \
        "array of (string or null)"
    assert _type_label({"const": "x"}, resolve) == 'exactly "x"'
    assert _type_label({"type": "object", "additionalProperties": {"type": "integer"}}, resolve) == "map of integer"
    assert _type_label({"type": "array", "prefixItems": [{"type": "string"}, {"type": "number"}]}, resolve) == \
        "array of [string, number]"
    assert _type_label({"allOf": [{"type": "string"}, {"enum": ["a"]}]}, resolve) == 'string and one of "a"'
    assert _type_label({"type": "string", "allOf": [{"minLength": 1}]}, resolve) == "string"     # constraints only
    assert _type_label({"type": "string", "anyOf": [{"minLength": 1}, {"pattern": "x"}]}, resolve) == "string"
    assert _type_label({"anyOf": [{"type": "string"}, {}]}, resolve) == "any"                  # one branch takes anything
    assert _type_label({"properties": {"a": {}}}, resolve) == "object"
    assert _type_label({"$ref": "other.json#/x"}, resolve) == "ref other.json#/x"
    assert _type_label({"enum": list("abcdefghij")}, resolve) == 'one of "a"|"b"|"c"|"d"|"e"|"f"|"g"|"h"|…'
    nested = {"type": "array", "items": {"type": "array", "items": {"type": "array", "items": {"type": "array",
                                                                                                "items": {"type": "integer"}}}}}
    assert _type_label(nested, resolve) == "array of array of array of array of …"
    assert _type_label({}, resolve) == "any" and _type_label("junk", resolve) == "any"


def test_a_malformed_reference_never_hangs_or_crashes():
    schema = {"definitions": {"a": {"$ref": "#/definitions/b"}, "b": {"$ref": "#/definitions/a"}, "t": True}}
    resolve = _resolver(schema)
    assert _type_label({"$ref": "#/definitions/a"}, resolve).startswith("ref #/definitions/")      # a loop stops
    assert _type_label({"$ref": "#/definitions/t"}, resolve) == "any"                              # a boolean schema
    assert _type_label({"$ref": "#/definitions/missing"}, resolve) == "any"
    block = contract_block("x", "x.schema.json", {"properties": {"loop": {"$ref": "#/definitions/a"}}, **schema})
    assert "loop ref #/definitions/" in block
    broken = contract_block("y", "y.schema.json", {"required": ["a"], "properties": {"a": {"$ref": "#/definitions/none"}}})
    assert broken.endswith("minimal valid shape: unavailable: the schema has a broken reference")


def test_a_top_level_array_without_a_type_still_lists_its_items():
    block = contract_block("z", "z.schema.json", {"properties": {"rows": {"items": {"type": "object", "required": ["id"],
                                                                                    "properties": {"id": {"type": "string"}}}}}})
    assert "each `rows` item: id* (* required)" in block and "`rows` item types: id string" in block
