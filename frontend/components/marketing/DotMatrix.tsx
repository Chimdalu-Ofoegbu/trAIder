"use client";

// =============================================================================
// frontend/components/marketing/DotMatrix.tsx — dot-matrix footer canvas.
// Ported from traider.js dotMatrix: samples the wordmark into a dot grid that
// shimmers and scatters under the cursor. Cleans up RAF + listeners on unmount.
// =============================================================================

import { useEffect, useRef } from "react";

interface Dot {
  x: number;
  y: number;
  ox: number;
  oy: number;
  vx: number;
  vy: number;
}

export function DotMatrix({ word = "trAIder" }: { word?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let W = 0;
    let H = 0;
    let DPR = 1;
    let dots: Dot[] = [];
    const mouse = { x: -1e4, y: -1e4 };
    const css = (v: string) =>
      getComputedStyle(document.documentElement).getPropertyValue(v).trim();

    const build = () => {
      DPR = Math.min(2, window.devicePixelRatio || 1);
      W = canvas.clientWidth;
      H = canvas.clientHeight;
      // Canvas not laid out yet (0×0): bail. getImageData(0,0,0,…) throws an
      // IndexSizeError that escapes the effect and crashes the whole page; the
      // ResizeObserver re-runs build() once the footer canvas has real size.
      if (W < 2 || H < 2) return;
      canvas.width = W * DPR;
      canvas.height = H * DPR;
      ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
      const off = document.createElement("canvas");
      off.width = W;
      off.height = H;
      const o = off.getContext("2d");
      if (!o) return;
      o.fillStyle = "#fff";
      o.textAlign = "center";
      o.textBaseline = "middle";
      const fs = Math.min(W / (word.length * 0.62), H * 0.74);
      o.font = `600 ${fs}px "Helvetica Neue", Helvetica, sans-serif`;
      o.fillText(word, W / 2, H / 2 + fs * 0.02);
      const img = o.getImageData(0, 0, W, H).data;
      dots = [];
      const gap = Math.max(7, Math.round(W / 150));
      for (let y = 0; y < H; y += gap) {
        for (let x = 0; x < W; x += gap) {
          const alpha = img[(y * W + x) * 4 + 3];
          if (alpha > 80) dots.push({ x, y, ox: x, oy: y, vx: 0, vy: 0 });
        }
      }
    };

    const onMove = (e: MouseEvent) => {
      const r = canvas.getBoundingClientRect();
      mouse.x = e.clientX - r.left;
      mouse.y = e.clientY - r.top;
    };
    const onLeave = () => {
      mouse.x = -1e4;
      mouse.y = -1e4;
    };
    canvas.addEventListener("mousemove", onMove);
    canvas.addEventListener("mouseleave", onLeave);
    // ResizeObserver (not window 'resize') so build() also fires the moment the
    // canvas transitions from 0×0 to its laid-out size on first paint.
    const ro = new ResizeObserver(() => build());
    ro.observe(canvas);

    let raf = 0;
    const t0 = performance.now();
    const frame = (t: number) => {
      ctx.clearRect(0, 0, W, H);
      const ink = css("--ink-2");
      const brand = css("--brand");
      const wave = (t - t0) / 1000;
      for (let i = 0; i < dots.length; i++) {
        const d = dots[i];
        const dx = d.x - mouse.x;
        const dy = d.y - mouse.y;
        const dist2 = dx * dx + dy * dy;
        if (dist2 < 9000) {
          const f = (9000 - dist2) / 9000;
          const dd = Math.sqrt(dist2) || 1;
          d.vx += (dx / dd) * f * 2.4;
          d.vy += (dy / dd) * f * 2.4;
        }
        d.vx += (d.ox - d.x) * 0.045;
        d.vy += (d.oy - d.y) * 0.045;
        d.vx *= 0.82;
        d.vy *= 0.82;
        d.x += d.vx;
        d.y += d.vy;
        const near = dist2 < 9000;
        const shimmer =
          0.45 + 0.35 * Math.sin(wave * 1.6 + d.ox * 0.02 + d.oy * 0.03);
        ctx.fillStyle = near ? brand : ink;
        ctx.globalAlpha = near ? 0.95 : shimmer;
        const r = near ? 2.2 : 1.5;
        ctx.beginPath();
        ctx.arc(d.x, d.y, r, 0, 7);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
      raf = requestAnimationFrame(frame);
    };

    build();
    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      canvas.removeEventListener("mousemove", onMove);
      canvas.removeEventListener("mouseleave", onLeave);
      ro.disconnect();
    };
  }, [word]);

  return <canvas ref={canvasRef} className="matrix-canvas" aria-label={word} />;
}
