# Wire artifacts and serialization boundaries

These are conditional technical examples. Apply the installed library, public contract, and actual environment to choose the relevant behavior. A worked case illustrates a mechanism; it is not evidence about the repository being repaired.

<a id="domain-wire-formats"></a>

<a id="domain-wire-formats-multipart-bodies-encoded-urls-and-structured-responses"></a>

### Wire formats: multipart bodies, encoded URLs, and structured responses

Read this before changing byte-level serializers or scanners. Protocol facts below come from the linked standards; the repair procedures and examples are authored reasoning. The application's current public contract chooses ordering, error behavior, supported inputs, and compatibility.

<a id="domain-wire-formats-multipart-structure-and-representation"></a>

<a id="domain-multipart-structure-and-representation"></a>

#### Multipart structure and representation

RFC 7578 defines form-data parts with a Content-Disposition containing a field name; file parts may include a filename. RFC 2046 supplies multipart boundary/delimiter framing. A boundary parameter and the delimiter bytes in the body must agree; the closing delimiter differs from an ordinary separator. Headers and bodies are separated by a blank line. MIME base64 output is limited to 76 encoded characters per line under RFC 2045; that is a protocol rule, not an arbitrary formatter width. Modern HTTP form-data discourages Content-Transfer-Encoding, while a legacy application can require a MIME serialization for compatibility. Preserve the current contract explicitly rather than silently choosing either policy. Sources: [RFC 7578](https://www.rfc-editor.org/rfc/rfc7578), [RFC 2046](https://www.rfc-editor.org/rfc/rfc2046), [RFC 2045](https://www.rfc-editor.org/rfc/rfc2045).

Build a semantic part model before rendering bytes: field name, optional display filename, payload bytes, media type, and transfer-encoding policy. Distinguish a plain text field from a file descriptor whose payload happens to be text. Distinguish absent content from an explicitly supplied empty payload. A truthiness check can accidentally read a file instead of sending the caller's empty content.

Resolve payload independently of metadata. The path used to read bytes and the filename sent to a receiver need not be the same. Do not expose a local directory path as a display filename by accident. Determine whether inline content, a referenced file, or both are allowed, and which source has precedence. Decide MIME type from explicit metadata, permitted inference, and the contract's fallback; a guesser that returns no type and one that raises are different failures to handle deliberately.

<a id="domain-wire-formats-library-serializers-and-exact-compatibility"></a>

<a id="domain-library-serializers-and-exact-compatibility"></a>

##### Library serializers and exact compatibility

Python's email package can add MIME headers, charset parameters, encodings, and policy-dependent line endings. Its policies control serialization behavior; MIME subclasses perform additional construction. Sources: [email policies](https://docs.python.org/3/library/email.policy.html), [MIME classes](https://docs.python.org/3/library/email.mime.html).

Consequently, calling a convenient text helper does not establish exact wire compatibility. Inspect the bytes it emits, including implicit MIME-Version or charset additions, header order, transfer encoding, CRLF versus LF, and final newline. If a fixture defines exact serialization, compare against that fixture after substituting only legitimately variable data such as a generated boundary. Do not normalize all whitespace or header order in the assertion when those are requirements.

```text
validate outer input and each part descriptor
resolve the payload source using presence, not truthiness
convert to bytes once at the documented encoding boundary
choose media type and transfer policy independently
construct allowed headers and serialize with the required policy
return the content type and the body generated with the same boundary
```

The algorithm does not prescribe field sorting, header order, a boundary spelling, or one universal transfer policy. Learn those from the current interface. Avoid flattening all errors into one exception if the API distinguishes bad types, missing content, unreadable files, and serializer failure.

<a id="domain-wire-formats-worked-checks"></a>

<a id="domain-worked-checks"></a>

##### Worked checks

An invented upload contains a Unicode text label, an empty inline binary field, and a disk-backed image whose display name excludes its directory. Parse the result with an independent MIME parser and assert names, payload bytes, filenames, media types, and number of parts. Also compare exact serialized bytes where compatibility requires it. Include bytes containing line breaks and boundary-like text. The parser check catches malformed framing; the byte check catches unwanted library normalization. Neither replaces the other.

<a id="domain-wire-formats-encoded-url-editing"></a>

<a id="domain-encoded-url-editing"></a>

#### Encoded URL editing

RFC 3986 gives percent-encoding the form `%` followed by two hexadecimal digits. Reserved delimiters can change component structure when decoded. Source: [RFC 3986](https://www.rfc-editor.org/rfc/rfc3986).

Represent a URL as components plus the encoded spelling required by the operation. Select the component or segment before looking for editable numbers. Preserve unrelated components and encoded octets. A regex over the whole displayed URL can change a port, query parameter, fragment, or the hex digits of an escape instead of the requested resource number.

An encoded scanner can advance over each valid `%HH` triplet as an indivisible token and recognize permitted digit runs outside those tokens. Whether a malformed percent sequence is rejected, preserved, or delegated to the URL parser is a contract decision. Do not repeatedly decode to make scanning easy: `%25` followed by digits is not permission to reinterpret the result as another escape.

For a chosen numeric span, separate numeric arithmetic from rendering. Discover signed/unsigned behavior, overflow/underflow policy, leading-zero width, and what happens when no eligible number exists. Replace only the chosen span, then serialize through an API/mode that preserves the encoded representation. An otherwise correct edit can be undone by a serializer that decodes or canonicalizes unrelated text.

For example, `/archive/0042/part%2Fblue?view=8#chapter3` has several digit sequences with different roles. The caller's operation determines the eligible component. A test should assert preservation of the escape and all unrelated components, not merely that some number increased.

<a id="domain-wire-formats-structured-service-responses"></a>

<a id="domain-structured-service-responses"></a>

#### Structured service responses

Follow request format, response decoding, document extraction, and error mapping as one path. A caller that switches a query parameter to request JSON must also use the JSON response envelope and preserve its public result shape. Distinguish malformed serialization, a valid service-error object, missing required fields, and an empty successful result. Avoid an overly broad catch that converts all four to “no results.”

Use independent fixtures for an ordinary result, an empty result, a malformed body, and a service error. Inspect the request sent to the fake service and the returned public model. Regex extraction from structured data can pass a simple fixture while losing nested values or escaped strings.

<a id="complete-wire-body"></a>

## Compare the complete wire artifact

A MIME message and its body are different artifacts. A library serializer may emit top-level MIME headers plus a blank line before the first multipart boundary, while an HTTP body contract expects the boundary at byte zero. Determine which representation the public interface returns before choosing an extraction method. Do not make a body-only check skip an unexpected header merely because the implementation emitted it.

Use a parser, documented serializer option, or verified header/body separator for extraction; blindly searching for a boundary substring can collide with content or mishandle preambles. Check the entire returned bytes with an independent expected fixture: first boundary, line endings, header ordering/policy, each part's payload and transfer encoding, closing delimiter, and final newline. A check derived from the same serializer can reproduce the same defect. For other formats, inspect the actual consumer-visible MIME/type, decoded object and bytes through the whole pipeline, rather than concluding format preservation from an encoder branch.

<a id="encoded-read-write-pair"></a>

## Pair encoded reads with the correct writer mode

Reading an encoded component and passing that text into a decoded-component setter can encode `%` again; `%3A` then becomes `%253A`. Conversely, decoding and rebuilding can change reserved delimiters or unrelated bytes. There is no universal "always use FullyEncoded" rule for Qt `QUrl`: the getter mode, setter parsing mode, component, Qt version and required preservation contract must agree. Follow any explicitly requested getter/representation contract.

Use the installed API's documented mode or a verified whole-URL encoded reconstruction when preserving encoded bytes. Check the resulting URL bytes independently after the setter, not just the intermediate string. Include encoded reserved characters, literal percent, Unicode, query/fragment boundaries, and unchanged neighboring text. A minimal example should establish that a no-op read/write preserves the required representation before adding arithmetic or replacement logic.
