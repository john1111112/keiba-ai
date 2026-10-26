"""Read-only COM registration probe; never calls JVInit or JVOpen."""
import json
import struct
import sys


def main():
    result = {"python_bitness": struct.calcsize("P") * 8, "pywin32_available": False, "com_creatable": False, "error": None}
    if result["python_bitness"] != 32:
        result["error"] = "Requires 32-bit interpreter"
    else:
        try:
            import win32com.client
            result["pywin32_available"] = True
            link = win32com.client.Dispatch("JVDTLab.JVLink")
            result["com_creatable"] = link is not None
        except Exception as exc:
            result["error"] = str(exc)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["com_creatable"] else 1


if __name__ == "__main__":
    sys.exit(main())
