"""Run the two pinned whole-chest models and save native-grid segmentation."""
import argparse
from pathlib import Path
from queue import SimpleQueue


def effusion(ct, output):
    from totalsegmentator.python_api import totalsegmentator
    from totalsegmentator.config import setup_totalseg, set_config_key

    setup_totalseg()
    set_config_key("send_usage_stats", False)
    totalsegmentator(ct, output, ml=True, task="pleural_pericard_effusion",
                    nr_thr_resamp=2, nr_thr_saving=1, device="gpu:0")


def parenchyma(ct, output, checkpoint):
    import numpy as np
    import SimpleITK as sitk
    import torch
    from medpseg.image_read import read_preprocess
    from medpseg.poly_pipeline import PolySegmentationPipeline, get_connected_components

    sitk.ProcessObject.SetGlobalDefaultNumberOfThreads(2)
    torch.set_num_threads(2)
    data, _, _, _, direction, image = read_preprocess(str(ct), norm=True)
    axes = np.asarray(direction).reshape(3, 3)
    if not np.allclose(axes, np.diag(np.diag(axes)), atol=1e-4, rtol=0):
        raise ValueError("MEDPSeg input orientation needs a validated reorientation adapter")
    model = PolySegmentationPipeline(weight=str(checkpoint), batch_size=2, output_dir=str(output.parent),
                                     post=False, torch_compile=False, cli=True)
    probabilities, airway, vessel = model(data[None, None], SimpleQueue(), None, image, "ct",
                                           np.asarray(direction), False)
    del data, airway, vessel
    if probabilities.shape[0] != 4 or not np.isfinite(probabilities).all():
        raise ValueError("MEDPSeg did not return four finite tissue class activation maps")
    lung = (probabilities[1:].sum(axis=0) > .5).astype(np.int32)
    if not lung.any():
        raise ValueError("MEDPSeg did not segment any lung")
    lung = get_connected_components(lung, return_largest=2)[0].astype(np.uint8)
    labels = np.argmax(probabilities, axis=0).astype(np.uint8)
    del probabilities
    labels *= lung
    restored = np.flip(labels, np.flatnonzero(np.diag(axes)[::-1] < 0)).copy()
    result = sitk.GetImageFromArray(restored)
    result.CopyInformation(image)
    sitk.WriteImage(result, str(output), True)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("model", choices=("effusion", "medpseg"))
    cli.add_argument("ct", type=Path)
    cli.add_argument("output", type=Path)
    cli.add_argument("--checkpoint", type=Path)
    args = cli.parse_args()
    if args.model == "effusion":
        effusion(args.ct, args.output)
    else:
        parenchyma(args.ct, args.output, args.checkpoint)
