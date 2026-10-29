# Launcher-only Python import paths

`Jobs.start` supports an opt-in `MADS_BOOT_PYTHONPATH` environment variable.
Launchers using `PYTHONPATH=/runtime` to boot a frozen harness may set
`MADS_BOOT_PYTHONPATH=/runtime` **before starting that interpreter**. A source
edit alone does not activate this change in a running process.

The marker is an `os.pathsep`-separated list of absolute, literal entries to
remove from `PYTHONPATH` in tool shell subprocesses. There is no normalization,
whitespace trimming, symlink resolution or guess based on a directory name.
Nonexistent absolute entries are allowed. Duplicates are harmless. Empty marker
components are ignored. Unset/empty marker means compatible behavior. A relative
nonempty marker component makes the entire declaration invalid: warn through
`mads.jobs` logging and leave `PYTHONPATH` unchanged, rather than partially strip.

Unmarked entries retain their exact spelling and order, including explicit
empty/current-directory entries. When every entry was boot-only, `PYTHONPATH`
is **unset**, not replaced by an invented empty entry. The parent environment is
not mutated. Existing credential filtering is unchanged. The marker itself is
retained in child environments; unset it when deliberately starting another
harness with a different import contract. This is import hygiene, not a security
sandbox; a shell command can explicitly set its own environment.

The shared `Jobs` implementation serves Course, OpenRouter and OpenAI adapters.
Only subprocess execution is affected, not in-process read/search/edit/write.
The standard assignment runner boots `/agent/madsLoop.py` by script path and
needs no declaration unless its launch configuration adds a boot `PYTHONPATH`.

Removal does not make every external test script automatically import the
workspace. Use a legitimate test setup, e.g. `python -m unittest discover -s
tests` from the workspace, or `PYTHONPATH="$PWD" python /outside/check.py` for an
external test that intentionally targets that source. Verify imported module
paths when a frozen copy could be involved.

`tests/test_boot_pythonpath.py` exercises **actual Jobs subprocesses**, with
external scripts, distinct frozen/edited modules, explicit user import entries,
parent immutability, synthetic credential filtering, malformed declarations,
and unset-versus-empty behavior. Run from the source under test:

```sh
python -m unittest discover -s tests -p test_boot_pythonpath.py -v
```
