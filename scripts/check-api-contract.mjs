#!/usr/bin/env node
// Extracts a coarse, JSON-serializable shape for a set of named top-level
// `type X = {...}` declarations out of a TypeScript source file, using a
// plain AST walk (no `ts.Program`, no tsconfig/module resolution) so it
// stays independent of the app's module graph. Used by
// backend/tests/test_type_contract.py as the TypeScript side of the P3.1a
// backend<->frontend contract-drift gate; see PLANS.md / docs/decisions
// for the full mechanism.

import { readFileSync } from "node:fs";
import ts from "typescript";

function usageError(message) {
  process.stderr.write(`${message}\n`);
  process.stderr.write(
    "usage: node scripts/check-api-contract.mjs <file.tsx> <TypeName> [TypeName...]\n",
  );
  process.exit(2);
}

const [, , filePath, ...typeNames] = process.argv;
if (!filePath || typeNames.length === 0) {
  usageError("Missing arguments.");
}

let sourceText;
try {
  sourceText = readFileSync(filePath, "utf8");
} catch (error) {
  usageError(`Could not read ${filePath}: ${error.message}`);
}

const sourceFile = ts.createSourceFile(
  filePath,
  sourceText,
  ts.ScriptTarget.Latest,
  /* setParentNodes */ true,
  ts.ScriptKind.TSX,
);

const aliases = new Map();
for (const statement of sourceFile.statements) {
  if (ts.isTypeAliasDeclaration(statement)) {
    aliases.set(statement.name.text, statement.type);
  }
}

// A "shape" is {kind, nullable, element?, properties?, opaque?}. `nullable`
// belongs to the value's own type (it can appear anywhere a type can, not
// just at object-property position). `optional` belongs to a *property*
// (a key can be absent from its containing object) and is attached only
// where a shape is stored as an object's property value -- see
// typeLiteralToShape. These are independent axes on purpose: a TS
// `field?: string` (optional, non-nullable) does not admit `null`, and is
// therefore NOT equivalent to a `field: string | null` field -- collapsing
// them was a real bug caught in review, not a style choice.
function typeNodeToShape(typeNode, visiting) {
  if (ts.isParenthesizedTypeNode(typeNode)) {
    return typeNodeToShape(typeNode.type, visiting);
  }

  if (ts.isUnionTypeNode(typeNode)) {
    let nullable = false;
    const rest = [];
    for (const member of typeNode.types) {
      // `null` as a *type* is a LiteralTypeNode wrapping a NullKeyword
      // token (like string/numeric literal types) -- it is NOT a bare
      // NullKeyword type node itself. `undefined` as a type, unlike
      // `null`, *is* its own keyword type node directly. Mixing these
      // two shapes up is an easy, silent way to make every `X | null`
      // field misclassify as nullable:false -- caught by manually
      // running this script against known fields (Project.brief,
      // RuntimeStatus.active_model) and comparing to the source.
      const isNullLiteral =
        ts.isLiteralTypeNode(member) && member.literal.kind === ts.SyntaxKind.NullKeyword;
      const isUndefinedKeyword = member.kind === ts.SyntaxKind.UndefinedKeyword;
      if (isNullLiteral || isUndefinedKeyword) {
        nullable = true;
        continue;
      }
      rest.push(member);
    }
    if (rest.length === 0) {
      return { kind: "unknown", nullable: true };
    }
    if (rest.length === 1) {
      const inner = typeNodeToShape(rest[0], visiting);
      return { ...inner, nullable: inner.nullable || nullable };
    }
    const allStringLiterals = rest.every(
      (member) => ts.isLiteralTypeNode(member) && ts.isStringLiteral(member.literal),
    );
    if (allStringLiterals) {
      return { kind: "string", nullable };
    }
    return { kind: "unknown", nullable };
  }

  if (typeNode.kind === ts.SyntaxKind.StringKeyword) {
    return { kind: "string", nullable: false };
  }
  if (typeNode.kind === ts.SyntaxKind.NumberKeyword) {
    return { kind: "number", nullable: false };
  }
  if (typeNode.kind === ts.SyntaxKind.BooleanKeyword) {
    return { kind: "boolean", nullable: false };
  }
  if (ts.isLiteralTypeNode(typeNode) && ts.isStringLiteral(typeNode.literal)) {
    return { kind: "string", nullable: false };
  }

  if (ts.isArrayTypeNode(typeNode)) {
    return { kind: "array", nullable: false, element: typeNodeToShape(typeNode.elementType, visiting) };
  }

  if (ts.isTypeLiteralNode(typeNode)) {
    return typeLiteralToShape(typeNode, visiting);
  }

  if (ts.isTypeReferenceNode(typeNode)) {
    const name = typeNode.typeName.getText(sourceFile);

    if (name === "Array" && typeNode.typeArguments?.length === 1) {
      return {
        kind: "array",
        nullable: false,
        element: typeNodeToShape(typeNode.typeArguments[0], visiting),
      };
    }
    if (name === "Record" && typeNode.typeArguments?.length === 2) {
      // Deliberately opaque -- the TS author already chose an index
      // signature over enumerating keys, and dict[str, Any]-style Python
      // fields don't constrain per-key shape either. Nothing to diff.
      return { kind: "object", nullable: false, opaque: true };
    }

    const referenced = aliases.get(name);
    if (referenced) {
      if (visiting.has(name)) {
        // No cycles among the target types today; guard anyway rather
        // than trust that forever.
        return { kind: "object", nullable: false };
      }
      visiting.add(name);
      const resolved = typeNodeToShape(referenced, visiting);
      visiting.delete(name);
      return resolved;
    }
    return { kind: "unknown", nullable: false };
  }

  return { kind: "unknown", nullable: false };
}

function typeLiteralToShape(typeLiteralNode, visiting) {
  const properties = {};
  for (const member of typeLiteralNode.members) {
    if (!ts.isPropertySignature(member) || !member.type) {
      continue;
    }
    const propertyName = member.name.getText(sourceFile);
    const optional = Boolean(member.questionToken);
    const shape = typeNodeToShape(member.type, visiting);
    properties[propertyName] = { optional, ...shape };
  }
  return { kind: "object", nullable: false, properties };
}

const result = {};
for (const name of typeNames) {
  const declared = aliases.get(name);
  result[name] = declared ? typeNodeToShape(declared, new Set([name])) : { error: "not found" };
}

process.stdout.write(JSON.stringify(result));
