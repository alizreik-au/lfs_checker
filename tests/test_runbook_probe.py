from __future__ import annotations

from runbook_probe import classify_push_output, parse_missing_pairs


def test_parse_missing_pairs_extracts_path_and_oid() -> None:
    text = """
Uploading LFS objects:  33% (1/3), 12 MB | 0 B/s
(missing) Content/Maps/Foo.umap (0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef)
(missing) Some path with spaces.uasset (AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA)
"""
    missing = parse_missing_pairs(text)
    assert missing == [
        {
            "path": "Content/Maps/Foo.umap",
            "oid": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        },
        {
            "path": "Some path with spaces.uasset",
            "oid": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        },
    ]


def test_parse_missing_pairs_respects_max_items() -> None:
    text = "\n".join(
        [
            "(missing) a (0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef)",
            "(missing) b (0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef)",
            "(missing) c (0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef)",
        ]
    )
    missing = parse_missing_pairs(text, max_items=2)
    assert [m["path"] for m in missing] == ["a", "b"]


def test_classify_push_output_prefers_auth() -> None:
    text = "Permission denied to git-receive-pack\n(missing) X (0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef)"
    assert classify_push_output(text, exit_code=1) == "auth_or_permissions"


def test_classify_push_output_non_fast_forward() -> None:
    text = "rejected non-fast-forward"
    assert classify_push_output(text, exit_code=1) == "git_non_fast_forward"


def test_classify_push_output_lfs_missing() -> None:
    text = "Git LFS upload missing objects\n(missing) X (0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef)"
    assert classify_push_output(text, exit_code=1) == "lfs_missing_local_objects"


def test_classify_push_output_ok_when_exit_code_zero() -> None:
    assert classify_push_output("Everything up-to-date", exit_code=0) == "ok"


def test_classify_push_output_unknown_when_no_match_and_nonzero() -> None:
    assert classify_push_output("error: failed to push some refs", exit_code=1) == "unknown_push_failure"

