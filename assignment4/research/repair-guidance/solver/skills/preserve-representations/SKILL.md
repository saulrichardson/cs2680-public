---
name: preserve-representations
description: Preserve meaning across encoding, conversion, wrapping, serialization, and reader/writer boundaries. Use when correct intermediate values become corrupted, metadata disappears, wire formats differ, or a changed result breaks its consumer.
---

# Follow the representation through the public operation

Two values can look alike in a log while differing in bytes, type, provenance, or how the next component interprets them. Use this skill when data crosses a boundary that can change those properties. The aim is to identify which distinctions the contract preserves and observe the complete transformation, including the caller that consumes the result.

## Describe what each stage receives

Name the representation before choosing a fix. A path string containing an escape sequence may represent literal characters or an already encoded delimiter. A request body may be bytes, text, a descriptor for a future serialization, or a wrapper whose field holds the body. A configuration number can be an ordinary integer or a tagged subclass whose provenance affects later trust decisions. Choosing an operation by appearance alone can silently erase these distinctions.

Trace the original input through the reader, transformation, wrapper, writer, and consumer. Look for the first stage that changes something the contract promises to retain. A broad read–transform–write diagram is useful only when it identifies actual functions and observed values. The repair may belong in a shared conversion, one caller, or the representation itself; moving it to every layer can create repeated decoding or inconsistent defaults.

## Exercise readers and writers as a pair

A getter that preserves encoded data and a setter that expects decoded data can each behave as documented while their composition corrupts the result. The decisive check reads using the chosen mode, performs the intended change, writes through the actual setter, and inspects the final encoded or serialized output. A copied string helper bypasses the API pair and cannot settle that question.

For an invented resource path, place an escaped delimiter beside a visible value that should change. Verify that the operation changes the visible value while retaining the delimiter's intended representation. Also consider a malformed escape if the public API defines its treatment. A successful round-trip through two functions that share the same mistaken normalization is weaker evidence than comparison against an independently justified wire representation.

Read [wire formats and complete serialization](references/wire-and-serialization.md) for multipart boundaries, header/body separation, URL modes, and decorated response objects. Those examples illustrate mechanisms; the installed library and current format specification determine exact options and bytes.

## Preserve type, identity, and metadata separately

Conversion is not merely obtaining an equal value. If the task preserves object identity when no conversion is necessary, constructing an equal replacement can be wrong. If a wrapper carries trust or source metadata, calling a built-in constructor can strip it. A collection has at least two places where metadata may live: on the collection and on individual elements. Testing one does not establish the other.

Build the conversion cases from the input family, requested output, required metadata, and failure behavior. Distinguish boolean subtypes from numeric values where the language requires it, and distinguish bytes from text before allowing an incidental decoder to choose behavior. A non-iterable input should reach the promised error contract rather than leaking an unrelated iteration exception. Error messages may need the caller's original requested alias even when conversion dispatch uses a normalized internal name.

The [coercion and provenance reference](references/coercion-and-metadata.md) develops these issues. It includes resource-creating conversions, where a new resource may correctly have different provenance from an ordinary converted value. The current contract decides whether tags propagate; the example is not a universal rule to copy metadata onto every output.

## Inspect wrappers before composing conventions

Frameworks often provide several legitimate response conventions. One handler may return a plain object for a serializer, another an already serialized string for a decorator, and another a completed response wrapper. Copying the outer structure of one example and the inner return of another can produce a double wrapper or a body of the wrong type.

Read the actual decorator or adapter and inspect the value a real caller receives. Check that a consumer can perform its promised next operation, such as decoding the body, iterating the fields, or reading the declared content type. A dictionary with the right keys inside an incompatible wrapper is not the same public result. Use a controlled handler call or request through the existing framework seam rather than inferring return semantics from a class attribute.

## Carry a representation change through its consumers

An optional result, newly structured identifier, or widened collection type changes downstream assumptions. Find direct calls, wrappers, alternate readers, serializers, and error paths. A function that now returns absence for an unsupported record must not leave a caller unconditionally dereferencing it. A parser supporting both binary and text input must expose equivalent field semantics where the public model promises equivalence.

Preserve absence until the component responsible for interpreting it. Replacing missing with an empty object can activate validation or conceal a required resolution step. Conversely, adding nullability without updating defaults and consumers creates a partial migration. The [configuration skill](../trace-configuration-and-interfaces/SKILL.md) explains propagation through loaders and constructors; [record parsing](../parse-and-match-records/SKILL.md) covers association and ordering before rendering.

## Check the final artifact at the right precision

Compare semantic values where the contract allows alternative encodings and exact bytes where it requires them. For multipart data, inspect the complete headers and body with the same boundary, filenames, transfer encoding, and content type. For a URL, inspect the encoded output after the real setter. For metadata, check the converted object and its members independently. For a response, inspect its public type and the operation the consumer performs next.

The distinction prevents both under-testing and unnecessary restrictions. An implementation need not reproduce an incidental formatting choice if the protocol permits alternatives. It must preserve a byte or type distinction when the caller relies on it. The most useful verification is a small observation that rejects the specific corruption the change could introduce.
