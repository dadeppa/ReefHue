"""Per-render context shared by all stages. No Qt."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RenderContext:
    #: processing pixels per full-resolution pixel (preview < 1, export = 1)
    scale: float = 1.0
    #: short side of the full-resolution geometry output; size-relative radii use it
    ref_short: int = 1000
    #: global statistics computed on the whole frame (e.g. dehaze airlight) so that
    #: region renders at 100 % match the full image
    stats: dict[str, Any] = field(default_factory=dict)
    #: resolves LUT files (milestone 6); ``None`` = LUT stages are skipped
    lut_provider: Any = None
    #: False while the crop is being edited (the UI shows the whole frame + overlay)
    apply_crop: bool = True

    def px(self, full_res_px: float) -> float:
        """A radius given in full-resolution pixels, at processing resolution."""
        return full_res_px * self.scale

    def rel(self, fraction_of_short_side: float) -> float:
        """A radius relative to the full image's short side, at processing resolution."""
        return fraction_of_short_side * self.ref_short * self.scale
