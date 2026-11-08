#!/usr/bin/env python3
"""One disposable Krita process; scripts execute on the application's Qt thread."""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

script = str(Path(sys.argv[1]).resolve())
with tempfile.TemporaryDirectory(prefix="joinery-krita-") as directory:
    root = Path(directory)
    plugin = root / "data/krita/pykrita/joinery_worker"
    plugin.mkdir(parents=True)
    plugin.with_suffix(".desktop").write_text("[Desktop Entry]\nType=Service\nServiceTypes=Krita/PythonPlugin\nX-KDE-Library=joinery_worker\nX-Python-2-Compatible=false\nName=Joinery Worker\n")
    plugin.joinpath("__init__.py").write_text('''from krita import Krita, Extension
from PyQt5.QtCore import QTimer
import os, traceback
class Worker(Extension):
    def setup(self):
        QTimer.singleShot(1000, self.run)
    def createActions(self, window):
        pass
    def run(self):
        try:
            path = os.environ["JOINERY_KRITA_SCRIPT"]
            exec(compile(open(path).read(), path, "exec"), {"__name__": "__main__"})
            open(os.environ["JOINERY_KRITA_RESULT"], "w").write("ok")
        except BaseException:
            open(os.environ["JOINERY_KRITA_RESULT"], "w").write(traceback.format_exc())
        # The supervisor terminates this disposable app after the document
        # has finished saving. Avoid unloading PyKrita from inside its callback.
Krita.instance().addExtension(Worker(Krita.instance()))
''')
    config = root / "config"
    config.mkdir()
    (config / "kritarc").write_text("[python]\nenable_joinery_worker=true\n")
    result_path = root / "result"
    env = dict(os.environ, XDG_DATA_HOME=str(root / "data"), XDG_CONFIG_HOME=str(config), JOINERY_KRITA_SCRIPT=script, JOINERY_KRITA_RESULT=str(result_path))
    result = subprocess.Popen(["krita", "--nosplash"], env=env)
    deadline = time.monotonic() + 600
    try:
        while not result_path.exists() and result.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        result_text = result_path.read_text() if result_path.exists() else "Krita did not complete its Python worker."
        if result.poll() is not None and result.returncode != 0:
            raise RuntimeError("Krita exited unexpectedly: " + str(result.returncode))
    finally:
        if result.poll() is None:
            result.terminate()
            try:
                result.wait(timeout=5)
            except subprocess.TimeoutExpired:
                result.kill()
                result.wait()
    if result_text != "ok":
        print(result_text, file=sys.stderr)
        sys.exit(1)
    sys.exit(0)
