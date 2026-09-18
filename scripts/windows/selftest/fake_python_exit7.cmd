@echo off
rem A stand-in interpreter that always fails with 7. Used to prove
rem gridiron_sync.ps1 propagates a child's exit code instead of logging
rem an empty one and returning success.
echo fake interpreter: failing on purpose 1>&2
exit /b 7
