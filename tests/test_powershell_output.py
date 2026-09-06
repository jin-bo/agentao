"""Reading a CLIXML-wrapped stream back as text.

Windows PowerShell 5.1 serialises a *redirected* error stream as CLIXML, and agentao always
redirects — so without this the model reads an XML envelope where the error message should
be. ``-OutputFormat Text`` does not prevent it, which is why this runs on the result rather
than being argued away at the launch.

The design constraint that shapes every test here: **no XML parser.** The text comes from a
subprocess running model-written code, and the standard library's parser expands internal
entities, so a two-hundred-byte document can become ten thousand characters. This repository
has no XML parser on any other path and does not gain one for this.
"""

from __future__ import annotations

import pytest

from agentao.capabilities.powershell import CLIXML_MARKER, extract

LIMIT = 40_000
HEADER = '<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">'


def wrapped(*elements: str, closed: bool = True) -> str:
    body = CLIXML_MARKER + "\n" + HEADER + "".join(elements)
    return body + ("</Objs>" if closed else "")


def test_ordinary_output_is_returned_untouched():
    """Most streams are not CLIXML, and this must be invisible to them."""
    plain = "error: something went wrong\n  at line 3\n"
    assert extract(plain, LIMIT) is plain


def test_the_text_of_a_wrapped_stream_comes_back():
    text = wrapped('<S S="Error">Remove-Item : Cannot find path</S>')
    assert extract(text, LIMIT) == "Remove-Item : Cannot find path"


def test_several_elements_come_back_in_order():
    """A multi-line error arrives as several elements, and their order is the message."""
    text = wrapped("<S>first</S>", "<S>second</S>", "<S>third</S>")
    assert extract(text, LIMIT) == "firstsecondthird"


def test_a_newline_survives_as_a_newline():
    """CLIXML writes a line break as ``_x000D__x000A_``; leaving it encoded would hand the
    model a single run-on line with visible escape sequences in it."""
    assert extract(wrapped("<S>a_x000D__x000A_b</S>"), LIMIT) == "a\r\nb"


def test_the_five_predefined_entities_are_decoded():
    text = wrapped("<S>&lt;tag&gt; &amp; &quot;q&quot; &apos;a&apos;</S>")
    assert extract(text, LIMIT) == "<tag> & \"q\" 'a'"


def test_a_numeric_character_reference_is_decoded():
    assert extract(wrapped("<S>&#x4E2D;&#25991;</S>"), LIMIT) == "中文"


def test_unescaping_happens_once_and_does_not_cascade():
    """``&amp;lt;`` is the literal text ``&lt;``, not ``<``.

    One pass over one regex covering both forms is what makes that true. A second pass — or
    two passes, entities then references — would let crafted output expand into something the
    length cap never measured.
    """
    assert extract(wrapped("<S>&amp;lt;</S>"), LIMIT) == "&lt;"


@pytest.mark.parametrize("reference", ["&#xD800;", "&#0;", "&#x110000;"])
def test_an_illegal_code_point_is_left_exactly_as_written(reference):
    """A surrogate, ``NUL`` and anything past the Unicode range are not legal XML.

    Decoding them would invent a character the producer could not have meant, and in the
    surrogate case would produce a string Python cannot encode.
    """
    assert extract(wrapped(f"<S>{reference}</S>"), LIMIT) == reference


def test_an_encoded_surrogate_is_left_alone_too():
    assert extract(wrapped("<S>_xD800_</S>"), LIMIT) == "_xD800_"


def test_text_before_the_marker_is_preserved():
    """A stream can carry real output before PowerShell starts wrapping."""
    text = "plain line\n" + wrapped("<S>wrapped</S>")
    assert extract(text, LIMIT) == "plain line\nwrapped"


# --------------------------------------------------------------- refusing to guess


def test_an_entity_declaration_is_never_expanded():
    """The billion-laughs shape. Reported and shown raw, never resolved."""
    text = (
        CLIXML_MARKER + "\n<!DOCTYPE Objs [<!ENTITY a 'aaaaaaaaaa'>]>"
        + HEADER + "<S>&a;&a;&a;</S></Objs>"
    )
    out = extract(text, LIMIT)
    assert "entity declaration" in out
    assert "aaaaaaaaaa" in out  # the declaration itself, shown; not its expansion
    assert "&a;&a;&a;" in out   # the reference, unexpanded


def test_a_truncated_wrapper_is_reported_rather_than_half_read():
    """Output can be cut by a timeout or a budget mid-envelope.

    Reading the elements that happen to be complete would silently hand back a fragment of an
    error as if it were the whole one.
    """
    out = extract(wrapped("<S>partial</S>", closed=False), LIMIT)
    assert "truncated" in out and CLIXML_MARKER in out


def test_a_wrapper_with_no_text_elements_is_reported():
    """An unknown structure — a newer schema, or something else entirely. Showing the raw
    envelope beats returning an empty string that reads as "no error"."""
    out = extract(wrapped("<Obj RefId=\"0\"><TN/></Obj>"), LIMIT)
    assert "no text elements" in out


# --------------------------------------------------------------------- the cap


def test_the_result_is_capped_even_when_extraction_succeeds():
    huge = wrapped(*[f"<S>{'x' * 1000}</S>" for _ in range(200)])
    out = extract(huge, limit=500)
    assert len(out) <= 600  # the cap, plus the omission note
    assert "omitted" in out


def test_the_raw_fallback_is_capped_too():
    """The path that shows the envelope is the path most likely to be enormous."""
    out = extract(wrapped("<S>y</S>" * 20_000, closed=False), limit=200)
    assert "omitted" in out and "truncated" in out
    assert len(out) < 400


# ------------------------------------------------------- every path that shows output


def test_the_timeout_path_unwraps_too(tmp_path):
    """A timed-out PowerShell command reports partial output, and that is a stream too.

    The formatted-result path had the extraction and this one did not, which is the shape of
    a guarantee that holds on one boundary: the model would read raw XML for exactly the
    commands that went wrong slowly.
    """
    from agentao.capabilities.shell import ShellResult
    from agentao.capabilities.shell_spec import AbsPath, ShellDialect, ShellSpec
    from agentao.tools.shell import ShellTool

    spec = ShellSpec(dialect=ShellDialect.POWERSHELL, interpreter=AbsPath("pwsh.exe"))
    envelope = wrapped("<S>slow failure</S>").encode("utf-8")

    class Timing:
        def run(self, request):
            return ShellResult(returncode=-1, stdout=b"", stderr=envelope, timed_out=True)

        def run_background(self, request):  # pragma: no cover - never reached
            raise AssertionError

    tool = ShellTool()
    tool.shell = Timing()
    out = tool._run_foreground("Start-Sleep 999", tmp_path, 1, spec)
    assert "timed out" in out
    assert "slow failure" in out
    assert CLIXML_MARKER not in out
