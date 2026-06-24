import sys

if "--no-gui" in sys.argv:
    from .main import run

    args = [a for a in sys.argv[1:] if a != "--no-gui"]
    run(args[0] if args else "config.yaml")
else:
    from .gui import VFSBotGUI

    app = VFSBotGUI()
    app.mainloop()
