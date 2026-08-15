import PyInstaller.__main__
import sys

def build(debug=False):
    print("Building FacultyMailer-Setup.exe...")

    args = [
        'main.py',
        '--onefile',
        '--name=FacultyMailer-Setup',
        '--clean',
        # pydantic_core is a compiled Rust extension (.pyd). 
        # Force PyInstaller to collect all binaries, submodules, and metadata.
        '--collect-all=pydantic',
        '--collect-all=pydantic_core',
        '--collect-all=google.genai',
    ]

    if debug:
        print("--> Debug mode enabled (console window active)")
        args.append('--console')
    else:
        args.append('--windowed')

    PyInstaller.__main__.run(args)
    print("\nBuild complete! Check the 'dist/' folder for FacultyMailer-Setup.exe.")

if __name__ == "__main__":
    is_debug = '--debug' in sys.argv
    build(debug=is_debug)