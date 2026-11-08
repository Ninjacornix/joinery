"""Diagnose hosted Windows WGL using native code beside verified private Mesa DLLs.

Requires the MSVC developer environment and 7-Zip. Does not build Godot, install
drivers, change registry/system DLLs, authenticate, or call an inference service.
Each case is a new process, preserving genuine executable-directory DLL search.
The JSON records diagnostic success/failure; it does not certify the editor GUI.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from release.windows_graphics import DLL_SHA256, sha256, verified_archive

SOURCE = r"""
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <cwchar>
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")

static void module_path(const char *key, HMODULE module) {
    wchar_t wide[32768] = {};
    char text[65536] = {};
    if (module && GetModuleFileNameW(module, wide, 32768)) {
        WideCharToMultiByte(CP_UTF8, 0, wide, -1, text, sizeof(text), nullptr, nullptr);
    }
    printf("%s=%s\n", key, text);
}
static void number(const char *key, long long value) { printf("%s=%lld\n", key, value); }

int main(int argc, char **argv) {
    setvbuf(stdout, nullptr, _IONBF, 0);
    const char *mode = argc > 1 ? argv[1] : "gdi-first";
    number("screen_width", GetSystemMetrics(SM_CXSCREEN));
    number("screen_height", GetSystemMetrics(SM_CYSCREEN));
    char station[512] = {};
    GetUserObjectInformationA(GetProcessWindowStation(), UOI_NAME, station, sizeof(station), nullptr);
    printf("window_station=%s\n", station);
    module_path("opengl_at_start", GetModuleHandleW(L"opengl32.dll"));

    HMODULE gl = nullptr;
    if (strcmp(mode, "gdi-first") != 0) {
        SetLastError(0);
        if (!strcmp(mode, "mesa-direct") || !strcmp(mode, "mesa-pixel-format")) {
            wchar_t private_path[32768] = {};
            GetModuleFileNameW(nullptr, private_path, 32768);
            wchar_t *filename = std::wcsrchr(private_path, L'\\');
            if (filename) {
                wcscpy_s(filename + 1, 32768 - static_cast<size_t>((filename + 1) - private_path), L"opengl32.dll");
                SetLastError(0);
                gl = LoadLibraryW(private_path);
            }
        } else {
            gl = LoadLibraryW(L"opengl32.dll");
        }
        number("preload_error", GetLastError());
        module_path("opengl_preloaded", gl);
    }
    HINSTANCE instance = GetModuleHandleW(nullptr);
    WNDCLASSW cls = {};
    cls.style = CS_OWNDC;
    cls.lpfnWndProc = DefWindowProcW;
    cls.hInstance = instance;
    cls.lpszClassName = L"JoineryWGLProbe";
    SetLastError(0);
    ATOM atom = RegisterClassW(&cls);
    number("register_class", atom);
    number("register_error", GetLastError());
    SetLastError(0);
    HWND window = CreateWindowExW(0, cls.lpszClassName, L"Joinery native CI graphics probe",
        WS_OVERLAPPEDWINDOW, 0, 0, 128, 128, nullptr, nullptr, instance, nullptr);
    number("create_window", window != nullptr);
    number("window_error", GetLastError());
    if (!window) return 0;
    SetLastError(0);
    HDC dc = GetDC(window);
    number("get_dc", dc != nullptr);
    number("dc_error", GetLastError());
    if (!dc) { DestroyWindow(window); return 0; }
    PIXELFORMATDESCRIPTOR pfd = {};
    pfd.nSize = sizeof(pfd);
    pfd.nVersion = 1;
    pfd.dwFlags = PFD_DRAW_TO_WINDOW | PFD_SUPPORT_OPENGL | PFD_DOUBLEBUFFER;
    pfd.iPixelType = PFD_TYPE_RGBA;
    pfd.cColorBits = 24;
    pfd.cDepthBits = 24;
    pfd.iLayerType = PFD_MAIN_PLANE;
    typedef int (WINAPI *ChoosePF)(HDC, const PIXELFORMATDESCRIPTOR *);
    typedef BOOL (WINAPI *SetPF)(HDC, int, const PIXELFORMATDESCRIPTOR *);
    ChoosePF choose = ChoosePixelFormat;
    SetPF set = SetPixelFormat;
    if (!strcmp(mode, "mesa-pixel-format") && gl) {
        choose = reinterpret_cast<ChoosePF>(GetProcAddress(gl, "wglChoosePixelFormat"));
        set = reinterpret_cast<SetPF>(GetProcAddress(gl, "wglSetPixelFormat"));
    }
    SetLastError(0);
    int format = choose ? choose(dc, &pfd) : 0;
    number("pixel_format", format);
    number("choose_error", GetLastError());
    module_path("opengl_after_choose", GetModuleHandleW(L"opengl32.dll"));
    SetLastError(0);
    BOOL configured = format && set ? set(dc, format, &pfd) : FALSE;
    number("set_pixel_format", configured);
    number("set_error", GetLastError());
    module_path("opengl_after_set", GetModuleHandleW(L"opengl32.dll"));
    // Godot configures this same window once in window_create and again when
    // constructing the first display/context. Record the repeated call too.
    SetLastError(0);
    BOOL configured_again = configured && set ? set(dc, format, &pfd) : FALSE;
    number("second_set_pixel_format", configured_again);
    number("second_set_error", GetLastError());
    SetLastError(0);
    if (!gl) gl = LoadLibraryW(L"opengl32.dll");
    number("load_error", GetLastError());
    module_path("opengl_loaded", gl);
    if (gl && configured) {
        typedef HGLRC (WINAPI *CreateContext)(HDC);
        typedef BOOL (WINAPI *MakeCurrent)(HDC, HGLRC);
        typedef BOOL (WINAPI *DeleteContext)(HGLRC);
        typedef PROC (WINAPI *GetProc)(LPCSTR);
        typedef HGLRC (WINAPI *CreateAttribs)(HDC, HGLRC, const int *);
        typedef const unsigned char *(WINAPI *GetString)(unsigned int);
        auto create = reinterpret_cast<CreateContext>(GetProcAddress(gl, "wglCreateContext"));
        auto make = reinterpret_cast<MakeCurrent>(GetProcAddress(gl, "wglMakeCurrent"));
        auto destroy = reinterpret_cast<DeleteContext>(GetProcAddress(gl, "wglDeleteContext"));
        auto get_proc = reinterpret_cast<GetProc>(GetProcAddress(gl, "wglGetProcAddress"));
        auto get_string = reinterpret_cast<GetString>(GetProcAddress(gl, "glGetString"));
        SetLastError(0);
        HGLRC legacy = create ? create(dc) : nullptr;
        number("legacy_context", legacy != nullptr);
        number("legacy_error", GetLastError());
        SetLastError(0);
        BOOL current = legacy && make ? make(dc, legacy) : FALSE;
        number("legacy_current", current);
        number("current_error", GetLastError());
        auto attribs = current && get_proc ? reinterpret_cast<CreateAttribs>(get_proc("wglCreateContextAttribsARB")) : nullptr;
        auto address = reinterpret_cast<uintptr_t>(attribs);
        if (address <= 3 || address == UINTPTR_MAX) attribs = nullptr;
        number("arb_create_context", attribs != nullptr);
        int attributes[] = {0x2091, 3, 0x2092, 3, 0x9126, 1, 0x2094, 2, 0};
        SetLastError(0);
        HGLRC core = attribs ? attribs(dc, nullptr, attributes) : nullptr;
        number("core33_context", core != nullptr);
        number("core_error", GetLastError());
        if (core && make && make(dc, core) && get_string) {
            auto renderer = get_string(0x1F01);
            auto version = get_string(0x1F02);
            printf("renderer=%s\nversion=%s\n", renderer ? reinterpret_cast<const char *>(renderer) : "",
                version ? reinterpret_cast<const char *>(version) : "");
        }
        if (make) make(dc, nullptr);
        if (core && destroy) destroy(core);
        if (legacy && destroy) destroy(legacy);
    }
    ReleaseDC(window, dc);
    DestroyWindow(window);
    UnregisterClassW(cls.lpszClassName, instance);
    number("probe_completed", 1);
    return 0;
}
"""


def prepare(directory: Path, cache: Path) -> Path:
    extractor = shutil.which("7z") or shutil.which("7zz")
    compiler = shutil.which("cl.exe")
    if not extractor or not compiler:
        raise ValueError("MSVC developer environment and 7-Zip are required")
    directory.mkdir(parents=True, exist_ok=False)
    archive = verified_archive(cache)
    subprocess.run(
        [
            extractor,
            "x",
            "-y",
            f"-o{directory}",
            str(archive),
            *("x64/" + name for name in DLL_SHA256),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    for name, expected in DLL_SHA256.items():
        source = directory / "x64" / name
        if sha256(source) != expected:
            raise ValueError("Unexpected Mesa DLL content: " + name)
        shutil.copyfile(source, directory / name)
    cpp = directory / "probe.cpp"
    cpp.write_text(SOURCE, encoding="utf-8")
    executable = directory / "probe.exe"
    subprocess.run(
        [
            compiler,
            "/nologo",
            "/EHsc",
            "/W4",
            str(cpp),
            "/Fe:" + str(executable),
            "/link",
            "/MANIFEST:EMBED",
        ],
        cwd=directory,
        check=True,
        timeout=60,
    )
    return executable


def probe(directory: Path, cache: Path) -> dict[str, Any]:
    if os.name != "nt":
        raise ValueError("This diagnostic requires actual native Windows")
    executable = prepare(directory.resolve(), cache.resolve())
    cases = {}
    modes = ("gdi-first", "mesa-preloaded", "mesa-direct", "mesa-pixel-format")
    variants = [(mode, False) for mode in modes] + [(mode, True) for mode in modes]
    marker = executable.with_name(executable.name + ".local")
    try:
        for mode, use_marker in variants:
            if use_marker:
                # Match the publisher's app-local deployment. The probe embeds a
                # manifest too, so the case observes whether Windows honors it.
                marker.write_text("dummy", encoding="ascii")
            else:
                marker.unlink(missing_ok=True)
            timed_out = False
            exit_code = None
            try:
                result = subprocess.run(
                    [str(executable), mode],
                    cwd=executable.parent,
                    env={**os.environ, "GALLIUM_DRIVER": "llvmpipe"},
                    check=False,
                    capture_output=True,
                    timeout=20,
                )
                output, errors = result.stdout, result.stderr
                exit_code = result.returncode
            except subprocess.TimeoutExpired as exc:
                output, errors = exc.stdout or b"", exc.stderr or b""
                timed_out = True
            fields: dict[str, Any] = {}
            for line in output.decode("utf-8", errors="replace").splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    fields[key] = int(value) if value.isdecimal() else value
            fields["exit_code"] = exit_code
            fields["timed_out"] = timed_out
            fields["dot_local_marker"] = use_marker
            fields["stderr"] = errors.decode("utf-8", errors="replace")[-4000:]
            cases[mode + ("-dot-local" if use_marker else "")] = fields
    finally:
        marker.unlink(missing_ok=True)
    return {"purpose": "native WGL diagnostic, not editor validation", "cases": cases}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directory", type=Path, required=True, help="Fresh private directory"
    )
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = probe(args.directory, args.cache)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
