"""Pinned, local TotalSegmentator runner for the five CT lung-lobe labels."""
import argparse
import os
from pathlib import Path

LOBE_NAMES = [
    "lung_upper_lobe_left",
    "lung_lower_lobe_left",
    "lung_upper_lobe_right",
    "lung_middle_lobe_right",
    "lung_lower_lobe_right",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("volume", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("weights", type=Path)
    args = parser.parse_args()

    os.environ["TOTALSEG_WEIGHTS_PATH"] = str(args.weights.resolve())
    os.environ["nnUNet_results"] = str(args.weights.resolve())
    os.environ["nnUNet_raw"] = str(args.weights.resolve())
    os.environ["nnUNet_preprocessed"] = str(args.weights.resolve())

    # Dataset291 is the organ part containing all five lobe classes. The
    # pinned upstream predictor maps its part labels to the total label IDs.
    from totalsegmentator.nnunet import nnUNet_predict_image
    import torch

    device = torch.device("cuda:0")
    torch.cuda.init()
    torch.cuda.set_device(device)
    torch.cuda.reset_peak_memory_stats(device)
    nnUNet_predict_image(
        args.volume.resolve(), args.output.resolve(), [291],
        model="3d_fullres", folds=[0], trainer="nnUNetTrainerNoMirroring",
        tta=False, multilabel_image=True, resample=1.5, crop=None, crop_path=None,
        task_name="total", roi_subset=LOBE_NAMES, nr_threads_resampling=1,
        nr_threads_saving=1, device=device, quiet=True,
    )
    if not args.output.is_file():
        raise RuntimeError("TotalSegmentator did not save the lobe mask.")
    print(f"peak_gpu_allocated_mib={torch.cuda.max_memory_allocated(device) / 2**20:.0f}")


if __name__ == "__main__":
    main()
