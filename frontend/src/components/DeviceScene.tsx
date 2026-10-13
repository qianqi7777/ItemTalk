import {
  Camera,
  Headphones,
  ScanLine,
  ShieldCheck,
  Speaker,
} from 'lucide-react';

/** 装饰性设备插画，不展示虚构的用户产品或识别结果。 */
export function DeviceScene() {
  return (
    <div className="device-scene" aria-hidden="true">
      <div className="scene-orbit orbit-one" />
      <div className="scene-orbit orbit-two" />
      <div className="scene-dot dot-one" />
      <div className="scene-dot dot-two" />
      <div className="scene-card scene-camera">
        <Camera size={51} strokeWidth={1.3} />
      </div>
      <div className="scene-card scene-headphones">
        <Headphones size={102} strokeWidth={1.2} />
        <span>HELLO, DEVICE.</span>
      </div>
      <div className="scene-card scene-speaker">
        <Speaker size={57} strokeWidth={1.3} />
      </div>
      <div className="scene-scan">
        <ScanLine size={19} />
        从一张照片开始
      </div>
      <div className="scene-source">
        <ShieldCheck size={18} />
        有来源，更可靠
      </div>
    </div>
  );
}
