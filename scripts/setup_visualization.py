"""Cache verified official right-hand geometry and pinned Three.js locally."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MODEL_URL = 'https://www.agibot.com/file/ueditor/php/upload/file/20260827/1787813106676328.zip'
MODEL_SHA256 = 'f4b48bbc28085370300496eac59a32f3bac497cdb9dda2447156c727c41fef34'
THREE_URL = 'https://registry.npmjs.org/three/-/three-0.170.0.tgz'
THREE_SHA256 = '4a608a355dcaba72e0e5383cdc814303f5b6060b43c238cdf6932dceb699238d'
LAYOUT_URL = 'https://raw.githubusercontent.com/AgibotTech/agillink_omnihand_sdk/v1.1.11/doc/pic/o10_tactile_sensor_array.jpg'
LAYOUT_SHA256 = '095f06f743ee33322709667e4a3ad3cb04353dd6212aaf93c597c1741fb5ce2f'

def vector(element, key, default):
    return [float(x) for x in element.get(key, default).split()]

def origin(element):
    if element is None:
        return {'xyz': [0,0,0], 'rpy': [0,0,0]}
    return {'xyz': vector(element, 'xyz', '0 0 0'), 'rpy': vector(element, 'rpy', '0 0 0')}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, help='Existing unmodified official right-hand URDF ZIP')
    args=parser.parse_args()
    dest=ROOT/'vendor/web_assets'
    model_dest=dest/'model'
    model_dest.mkdir(parents=True, exist_ok=True)
    archive=args.archive.read_bytes() if args.archive else urllib.request.urlopen(MODEL_URL,timeout=60).read()
    if hashlib.sha256(archive).hexdigest()!=MODEL_SHA256:
        raise ValueError('Official model archive differs from the pinned 2026-08-27 package')
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        names=z.namelist()
        urdf_name=next(n for n in names if n.endswith('/urdf/OmniHandright4.urdf'))
        root=ET.fromstring(z.read(urdf_name))
        links=[]
        for link in root.findall('link'):
            visuals=[]
            for vis in link.findall('visual'):
                mesh=vis.find('geometry/mesh')
                if mesh is None: continue
                filename=PurePosixPath(mesh.get('filename')).name
                mesh_path=next(n for n in names if n.endswith('/meshes/'+filename))
                (model_dest/filename).write_bytes(z.read(mesh_path))
                visuals.append({**origin(vis.find('origin')), 'mesh':filename, 'scale':vector(mesh,'scale','1 1 1')})
            links.append({'name':link.get('name'), 'visuals':visuals})
        joints=[]
        for joint in root.findall('joint'):
            axis=joint.find('axis')
            joints.append({**origin(joint.find('origin')), 'name':joint.get('name'), 'type':joint.get('type'),
                'parent':joint.find('parent').get('link'), 'child':joint.find('child').get('link'),
                'axis':vector(axis,'xyz','1 0 0') if axis is not None else [1,0,0]})
        model={'hand':'right','source':MODEL_URL,'source_sha256':MODEL_SHA256,'links':links,'joints':joints,
            'pose_note':'10 measured actuator positions -> official SDK solver -> 16 joint angles; passive joints are calculated, not measured. Middle ab/ad is fixed at zero.'}
        (model_dest/'model.json').write_text(json.dumps(model,indent=2),encoding='utf-8')
    three_dest=dest/'three'
    three_dest.mkdir(exist_ok=True)
    package=urllib.request.urlopen(THREE_URL,timeout=60).read()
    if hashlib.sha256(package).hexdigest()!=THREE_SHA256:
        raise ValueError('Three.js archive does not match the pinned package')
    # Only these four known files are extracted; archive paths are never used as destinations.
    members={'package/build/three.module.js':'three.module.js',
             'package/examples/jsm/controls/OrbitControls.js':'OrbitControls.js',
             'package/examples/jsm/loaders/STLLoader.js':'STLLoader.js', 'package/LICENSE':'LICENSE'}
    with tarfile.open(fileobj=io.BytesIO(package),mode='r:gz') as t:
        for source,target in members.items():
            (three_dest/target).write_bytes(t.extractfile(source).read())
    layout=urllib.request.urlopen(LAYOUT_URL,timeout=45).read()
    if hashlib.sha256(layout).hexdigest()!=LAYOUT_SHA256:
        raise ValueError('Official tactile index diagram differs from the pinned image')
    (dest/'tactile').mkdir(exist_ok=True)
    (dest/'tactile/o10_tactile_sensor_array.jpg').write_bytes(layout)
    manifest={'three_version':'0.170.0','three_package_url':THREE_URL,'three_package_sha256':hashlib.sha256(package).hexdigest(),
        'model_url':MODEL_URL,'model_archive_sha256':MODEL_SHA256,'finger_layout_source':LAYOUT_URL,
        'files':{str(p.relative_to(dest)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in dest.rglob('*') if p.is_file() and p.name!='manifest.json'}}
    (dest/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(f'Cached {len(links)} links and {len(joints)} joints; Three.js 0.170.0. No hardware opened.')

if __name__=='__main__': main()
