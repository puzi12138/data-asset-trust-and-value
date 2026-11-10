"""Read-only, privacy-minimized resource and dependency preflight."""
from __future__ import annotations
import importlib.metadata as metadata
import json,platform,sys
import psutil

GIB=1024**3
PACKAGES=('numpy','scipy','scikit-learn','psutil','threadpoolctl','xlrd')

def snapshot():
    mem=psutil.virtual_memory();packages={}
    for name in PACKAGES:
        try:packages[name]=metadata.version(name)
        except metadata.PackageNotFoundError:packages[name]=None
    os_name=platform.system()
    return {'schema_version':'1.0','python_version':platform.python_version(),
            'python_implementation':platform.python_implementation(),
            'os':os_name,'os_release':platform.release(),
            'architecture':platform.machine(),'cpu_model':platform.processor() or 'UNKNOWN',
            'cpu_logical_count':psutil.cpu_count(logical=True),
            'system_memory_total_bytes':mem.total,'system_memory_available_bytes':mem.available,
            'gpu':{'used':False,'model':'NOT_USED','memory_bytes':None},'device':'cpu',
            'packages':packages,'resource_platform':('WINDOWS_JOB' if os_name=='Windows' else
                'LINUX_EXPERIMENTAL_UNVERIFIED' if os_name=='Linux' else 'UNSUPPORTED'),
            'recording_scope':'No hostname, account name, absolute paths, or external credentials.'}

def main():
    snap=snapshot();snap['scientific_preflight_memory_ok']=snap['system_memory_available_bytes']>=int(1.5*GIB)
    print(json.dumps(snap,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':sys.exit(main())
