import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { Icon } from "../icons";
import {
  panImageTransform,
  pinchImageTransform,
  type ImagePoint,
  type ImageTransform,
} from "../image-gesture";

const CLOSE_ANIMATION_MS = 170;
const DRAG_THRESHOLD = 5;

interface GestureStart {
  transform: ImageTransform;
  points: Array<{ id: number; point: ImagePoint }>;
}

export function ImageLightbox({ src, alt, onClose }: {
  src: string;
  alt: string;
  onClose: () => void;
}) {
  const stageRef = useRef<HTMLDivElement>(null);
  const imageRef = useRef<HTMLImageElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const pointers = useRef(new Map<number, ImagePoint>());
  const gestureStart = useRef<GestureStart | null>(null);
  const transformRef = useRef<ImageTransform>({ scale: 1, x: 0, y: 0 });
  const suppressClick = useRef(false);
  const closeTimer = useRef<number | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const [transform, setTransform] = useState<ImageTransform>(transformRef.current);
  const [entered, setEntered] = useState(false);
  const [interacting, setInteracting] = useState(false);

  const applyTransform = useCallback((next: ImageTransform) => {
    transformRef.current = next;
    setTransform(next);
  }, []);

  const requestClose = useCallback(() => {
    if (closeTimer.current !== null) return;
    setEntered(false);
    closeTimer.current = window.setTimeout(
      () => onCloseRef.current(), CLOSE_ANIMATION_MS);
  }, []);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => setEntered(true));
    closeRef.current?.focus({ preventScroll: true });
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") requestClose();
    };
    window.addEventListener("keydown", keydown);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("keydown", keydown);
      if (closeTimer.current !== null) window.clearTimeout(closeTimer.current);
    };
  }, [requestClose]);

  const sizes = () => ({
    image: {
      width: imageRef.current?.clientWidth ?? 0,
      height: imageRef.current?.clientHeight ?? 0,
    },
    viewport: {
      width: stageRef.current?.clientWidth ?? window.innerWidth,
      height: stageRef.current?.clientHeight ?? window.innerHeight,
    },
  });

  const resetGestureStart = () => {
    gestureStart.current = {
      transform: transformRef.current,
      points: Array.from(pointers.current, ([id, point]) => ({ id, point })),
    };
  };

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (pointers.current.size === 0) suppressClick.current = false;
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    event.currentTarget.setPointerCapture(event.pointerId);
    if (pointers.current.size > 1) suppressClick.current = true;
    resetGestureStart();
    setInteracting(true);
  };

  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!pointers.current.has(event.pointerId)) return;
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    const start = gestureStart.current;
    if (!start) return;
    const current = Array.from(pointers.current, ([id, point]) => ({ id, point }));
    const { image, viewport } = sizes();
    if (current.length >= 2 && start.points.length >= 2) {
      suppressClick.current = true;
      applyTransform(pinchImageTransform(
        start.transform,
        start.points[0].point,
        start.points[1].point,
        current[0].point,
        current[1].point,
        image,
        viewport,
      ));
      return;
    }
    if (current.length !== 1 || start.points.length !== 1) return;
    const deltaX = current[0].point.x - start.points[0].point.x;
    const deltaY = current[0].point.y - start.points[0].point.y;
    if (Math.hypot(deltaX, deltaY) > DRAG_THRESHOLD) suppressClick.current = true;
    if (start.transform.scale > 1) {
      applyTransform(panImageTransform(
        start.transform, deltaX, deltaY, image, viewport));
    }
  };

  const finishPointer = (event: ReactPointerEvent<HTMLDivElement>, cancelled: boolean) => {
    if (!pointers.current.has(event.pointerId)) return;
    pointers.current.delete(event.pointerId);
    if (cancelled) suppressClick.current = true;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    if (pointers.current.size > 0) resetGestureStart();
    else {
      gestureStart.current = null;
      setInteracting(false);
    }
  };

  return <div ref={stageRef}
    className={`image-lightbox${entered ? " entered" : ""}${interacting ? " interacting" : ""}`}
    role="dialog" aria-modal="true" aria-label="图片预览"
    onPointerDown={onPointerDown} onPointerMove={onPointerMove}
    onPointerUp={(event) => finishPointer(event, false)}
    onPointerCancel={(event) => finishPointer(event, true)}
    onClick={(event) => {
      if (suppressClick.current) {
        event.preventDefault();
        return;
      }
      requestClose();
    }}>
    <div className="image-lightbox-content">
      <img ref={imageRef} className="image-lightbox-image" src={src} alt={alt}
        draggable={false}
        style={{ transform: `translate3d(${transform.x}px,${transform.y}px,0) scale(${transform.scale})` }}
        onDragStart={(event) => event.preventDefault()} />
    </div>
    <button ref={closeRef} type="button" className="image-lightbox-close"
      aria-label="关闭图片预览"
      onPointerDown={(event) => event.stopPropagation()}
      onClick={(event) => { event.stopPropagation(); requestClose(); }}>
      <Icon name="close" size={22} />
    </button>
  </div>;
}
