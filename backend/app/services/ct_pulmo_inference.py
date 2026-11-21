"""Pulmo 2.5D concept-head adapter for research QA.

Architecture and preprocessing adapted from ariyul/Pulmo revision 745873a
(CC BY 4.0). The adapter includes only the encoder and concept head.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from numpy._core.multiarray import scalar
import SimpleITK as sitk
import torch
from torch import nn

MODEL = "ariyul/Pulmo:745873a"
WEIGHTS_SHA256 = "abf60955ed20f873172dece5c1e515c7fea1f8fc7b87bf15951be08cfd5534f2"
CONCEPTS = ("subtlety", "internal_structure", "calcification", "sphericity", "margin",
            "lobulation", "spiculation", "texture")


class ResidualBlock(nn.Module):
    def __init__(self, in_channels, out_channels):
        super().__init__()
        self.conv1 = nn.Conv2d(in_channels, out_channels, 3, padding=1, bias=False)
        self.norm1 = nn.InstanceNorm2d(out_channels)
        self.conv2 = nn.Conv2d(out_channels, out_channels, 3, padding=1, bias=False)
        self.norm2 = nn.InstanceNorm2d(out_channels)
        self.act = nn.LeakyReLU(0.1, inplace=True)
        self.skip = (nn.Conv2d(in_channels, out_channels, 1, bias=False)
                     if in_channels != out_channels else nn.Identity())

    def forward(self, x):
        residual = self.skip(x)
        x = self.act(self.norm1(self.conv1(x)))
        return self.act(self.norm2(self.conv2(x)) + residual)


class Encoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.stem = ResidualBlock(7, 24)
        self.down1 = nn.Sequential(nn.MaxPool2d(2), ResidualBlock(24, 48))
        self.down2 = nn.Sequential(nn.MaxPool2d(2), ResidualBlock(48, 96))
        self.down3 = nn.Sequential(nn.MaxPool2d(2), ResidualBlock(96, 192))
        self.bottom = nn.Sequential(nn.MaxPool2d(2), ResidualBlock(192, 384))
        self.global_pool = nn.AdaptiveAvgPool2d(1)

    def forward(self, x):
        x = self.bottom(self.down3(self.down2(self.down1(self.stem(x)))))
        return self.global_pool(x).flatten(1)


class ConceptModel(nn.Module):
    def __init__(self):
        super().__init__()
        self.cnn = Encoder()
        self.concept_head = nn.Sequential(
            nn.LayerNorm(384), nn.Linear(384, 256), nn.GELU(),
            nn.Dropout(0.3), nn.Linear(256, len(CONCEPTS)),
        )

    def forward(self, x):
        return self.concept_head(self.cnn(x))


def patch(volume, center):
    corner = np.rint(center).astype(int) - [3, 32, 32]
    end = corner + [7, 64, 64]
    start_in_image = np.maximum(corner, 0)
    end_in_image = np.minimum(end, volume.shape)
    if np.any(start_in_image >= end_in_image):
        raise ValueError("Candidate falls outside the input volume")
    read = tuple(slice(a, b) for a, b in zip(start_in_image, end_in_image))
    write = tuple(slice(a, b) for a, b in zip(start_in_image - corner, end_in_image - corner))
    cropped = np.full((7, 64, 64), -1000, dtype=np.float32)
    cropped[write] = volume[read]
    return (np.clip(cropped, -1000, 1000) + 1000) / 2000


def load_model(weights):
    with weights.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != WEIGHTS_SHA256:
            raise ValueError("Unexpected Pulmo checkpoint digest")
    allowed = [np.dtype, scalar, np.dtypes.Float64DType, np.dtypes.Float32DType, np.dtypes.Int64DType]
    with torch.serialization.safe_globals(allowed):
        checkpoint = torch.load(weights, map_location="cpu", weights_only=True)
    model = ConceptModel().eval()
    expected = model.state_dict()
    model.load_state_dict({name: checkpoint["model_state_dict"][name] for name in expected})
    return model, float(checkpoint["val"]["r2"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("volume", type=Path)
    parser.add_argument("weights", type=Path)
    parser.add_argument("coordinates", type=Path)
    args = parser.parse_args()
    torch.set_num_threads(2)
    image = sitk.ReadImage(str(args.volume))
    array = sitk.GetArrayViewFromImage(image)
    targets = json.loads(args.coordinates.read_text(encoding="utf-8"))
    model, validation_r2 = load_model(args.weights)
    patches = [
        patch(array, image.TransformPhysicalPointToContinuousIndex(item["center_lps"])[::-1])
        for item in targets
    ]
    with torch.inference_mode():
        outputs = model(torch.from_numpy(np.stack(patches))).tolist()
    report = {"model": MODEL, "checkpoint_sha256": WEIGHTS_SHA256, "checkpoint_concept_validation_r2": validation_r2,
              "predictions": {target["id"]: dict(zip(CONCEPTS, scores)) for target, scores in zip(targets, outputs)}}
    print(json.dumps(report, allow_nan=False))


if __name__ == "__main__":
    main()
