from livegraph.mcp.diff_parser import parse_diff


SIMPLE_MODIFY = """\
diff --git a/livegraph/foo.py b/livegraph/foo.py
index abc..def 100644
--- a/livegraph/foo.py
+++ b/livegraph/foo.py
@@ -10,7 +10,9 @@ def existing_thing():
     return 1


-def changed_function():
-    return 2
+def changed_function():
+    return "two"
+
+def new_function():
+    return 3
"""


def test_parses_single_file_modify():
    result = parse_diff(SIMPLE_MODIFY)
    # Walk with current_new_line=10:
    #   10: '     return 1'          context, advance -> 11
    #   11: ''                       blank context, advance -> 12
    #   12: ''                       blank context, advance -> 13
    #   '-def changed_function():'   deletion, no advance (current=13)
    #   '-    return 2'              deletion, no advance (current=13)
    #   '+def changed_function():'   MARK 13, advance -> 14
    #   '+    return "two"'          MARK 14, advance -> 15
    #   '+'                          MARK 15, advance -> 16
    #   '+def new_function():'       MARK 16, advance -> 17
    #   '+    return 3'              MARK 17, advance -> 18
    assert result == {"livegraph/foo.py": {13, 14, 15, 16, 17}}


def test_parses_multiple_files_in_one_diff():
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x = 1\n"
        "+x = 2\n"
        "diff --git a/b.py b/b.py\n"
        "--- a/b.py\n"
        "+++ b/b.py\n"
        "@@ -5,1 +5,1 @@\n"
        "-y = 1\n"
        "+y = 2\n"
    )
    assert parse_diff(diff) == {"a.py": {1}, "b.py": {5}}


def test_parses_multi_hunk_in_single_file():
    diff = (
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old1\n"
        "+new1\n"
        "@@ -10,1 +10,1 @@\n"
        "-old10\n"
        "+new10\n"
    )
    assert parse_diff(diff) == {"x.py": {1, 10}}


def test_new_file_addition_marks_all_added_lines():
    diff = (
        "--- /dev/null\n"
        "+++ b/new.py\n"
        "@@ -0,0 +1,3 @@\n"
        "+def f():\n"
        "+    return 1\n"
        "+\n"
    )
    assert parse_diff(diff) == {"new.py": {1, 2, 3}}


def test_deleted_file_is_skipped():
    diff = (
        "--- a/gone.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-def gone():\n"
        "-    pass\n"
    )
    assert parse_diff(diff) == {}


def test_binary_diff_produces_no_entry():
    diff = (
        "diff --git a/img.png b/img.png\n"
        "Binary files a/img.png and b/img.png differ\n"
    )
    assert parse_diff(diff) == {}


def test_empty_diff_returns_empty_dict():
    assert parse_diff("") == {}


def test_normalizes_windows_path_separators():
    diff = (
        "--- a/pkg\\sub\\m.py\n"
        "+++ b/pkg\\sub\\m.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    assert parse_diff(diff) == {"pkg/sub/m.py": {1}}


def test_skips_no_newline_at_end_marker():
    diff = (
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "\\ No newline at end of file\n"
        "+y\n"
    )
    assert parse_diff(diff) == {"a.py": {1}}


def test_garbage_input_is_tolerated():
    assert parse_diff("complete garbage\nwith no diff headers\n") == {}
