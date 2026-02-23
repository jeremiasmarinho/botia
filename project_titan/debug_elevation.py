"""Check if LDPlayer is running elevated (admin)."""
import ctypes, ctypes.wintypes as wt

# Process access rights
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008

kernel32 = ctypes.windll.kernel32
advapi32 = ctypes.windll.advapi32

# Find LDPlayer PID
import subprocess
result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq dnplayer.exe', '/FO', 'CSV', '/NH'],
                       capture_output=True, text=True)
print(f"dnplayer.exe: {result.stdout.strip()}")

# Try to open LDPlayer process
pid = 43276  # from earlier Get-Process
print(f"\nChecking PID {pid}...")

# Try PROCESS_QUERY_LIMITED_INFORMATION (works cross-elevation)
handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
if not handle:
    err = ctypes.GetLastError()
    print(f"  OpenProcess failed: error {err}")
    if err == 5:
        print("  ACCESS DENIED - LDPlayer is likely running ELEVATED")
else:
    print(f"  OpenProcess succeeded: handle={handle}")
    
    # Check if elevated
    token = wt.HANDLE()
    ok = ctypes.windll.advapi32.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token))
    if ok:
        # Query TokenElevation
        class TOKEN_ELEVATION(ctypes.Structure):
            _fields_ = [('TokenIsElevated', ctypes.c_ulong)]
        
        te = TOKEN_ELEVATION()
        ret_len = ctypes.c_ulong()
        ok2 = advapi32.GetTokenInformation(
            token, 20,  # TokenElevation = 20
            ctypes.byref(te), ctypes.sizeof(te), ctypes.byref(ret_len)
        )
        if ok2:
            print(f"  TokenIsElevated: {te.TokenIsElevated}")
            if te.TokenIsElevated:
                print("\n  *** LDPlayer IS RUNNING AS ADMIN ***")
                print("  *** UIPI blocks SendInput from non-admin processes ***")
                print("  *** SOLUTION: Run Python script as Administrator ***")
            else:
                print("  LDPlayer is NOT elevated")
        else:
            print(f"  GetTokenInformation failed: {ctypes.GetLastError()}")
        kernel32.CloseHandle(token)
    else:
        print(f"  OpenProcessToken failed: {ctypes.GetLastError()}")
    
    kernel32.CloseHandle(handle)

# Also check LDPlayer services
print("\n--- LDPlayer service processes ---")
for proc_name in ['Ld9BoxHeadless.exe', 'Ld9BoxSVC.exe']:
    result2 = subprocess.run(['tasklist', '/FI', f'IMAGENAME eq {proc_name}', '/FO', 'CSV', '/NH'],
                            capture_output=True, text=True)
    if result2.stdout.strip():
        print(f"  {proc_name}: {result2.stdout.strip()}")
