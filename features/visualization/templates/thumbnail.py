"""슬라이드 그리드 썸네일 생성."""

import math
from collections.abc import Sequence
from pathlib import Path


class PillowThumbnailBuilder:
    """Pillow 기반 그리드 thumbnail.jpg 생성기."""

    def __init__(
        self,
        *,
        cell_width: int = 360,
        cell_height: int = 220,
        gap: int = 16,
        max_columns: int = 4,
    ) -> None:
        self.cell_width = cell_width
        self.cell_height = cell_height
        self.gap = gap
        self.max_columns = max_columns

    def build(self, slide_images: Sequence[Path], output_path: Path) -> Path:
        """슬라이드 이미지를 그리드로 배치해 JPG 썸네일을 만든다."""
        if not slide_images:
            raise ValueError("thumbnail.jpg를 만들 슬라이드 이미지가 없습니다.")

        try:
            from PIL import Image, ImageDraw, ImageOps
        except ImportError as exc:
            raise RuntimeError(
                "thumbnail.jpg 생성을 위해 Pillow가 필요합니다. "
                "uv sync --group template-tools 후 다시 실행하세요."
            ) from exc

        columns = min(self.max_columns, math.ceil(math.sqrt(len(slide_images))))
        rows = math.ceil(len(slide_images) / columns)
        label_height = 28
        width = self.gap + columns * (self.cell_width + self.gap)
        height = self.gap + rows * (self.cell_height + label_height + self.gap)
        canvas = Image.new("RGB", (width, height), color="white")
        draw = ImageDraw.Draw(canvas)

        for index, image_path in enumerate(slide_images):
            row = index // columns
            column = index % columns
            x = self.gap + column * (self.cell_width + self.gap)
            y = self.gap + row * (self.cell_height + label_height + self.gap)

            with Image.open(image_path) as image:
                preview = ImageOps.contain(
                    image.convert("RGB"), (self.cell_width, self.cell_height)
                )
                offset_x = x + (self.cell_width - preview.width) // 2
                offset_y = y + (self.cell_height - preview.height) // 2
                canvas.paste(preview, (offset_x, offset_y))

            draw.rectangle(
                [x, y, x + self.cell_width, y + self.cell_height],
                outline=(220, 220, 220),
                width=1,
            )
            draw.text((x, y + self.cell_height + 6), f"Slide {index + 1}", fill=(80, 80, 80))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output_path, format="JPEG", quality=88, optimize=True)
        return output_path
