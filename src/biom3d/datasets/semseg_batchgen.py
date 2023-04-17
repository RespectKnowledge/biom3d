#---------------------------------------------------------------------------
# Dataloader with batch_generator.
# Follow the nnUNet augmentation pipeline.
#---------------------------------------------------------------------------

import os
import random
import numpy as np
import pandas as pd

from batchgenerators.dataloading.single_threaded_augmenter import SingleThreadedAugmenter
from batchgenerators.transforms.abstract_transforms import AbstractTransform, Compose
from batchgenerators.transforms.color_transforms import BrightnessMultiplicativeTransform, \
    ContrastAugmentationTransform, GammaTransform
from batchgenerators.transforms.noise_transforms import GaussianNoiseTransform, GaussianBlurTransform
from batchgenerators.transforms.resample_transforms import SimulateLowResolutionTransform
from batchgenerators.transforms.spatial_transforms import SpatialTransform, MirrorTransform
from batchgenerators.transforms.utility_transforms import RemoveLabelTransform, RenameTransform, NumpyToTensor
from batchgenerators.transforms.crop_and_pad_transforms import CenterCropTransform
from batchgenerators.utilities.file_and_folder_operations import join, load_json, isfile, save_json, maybe_mkdir_p
from torch._dynamo import OptimizedModule
from batchgenerators.augmentations.utils import resize_segmentation
from batchgenerators.augmentations.utils import rotate_coords_3d, rotate_coords_2d
from batchgenerators.dataloading.data_loader import SlimDataLoaderBase
from batchgenerators.dataloading.multi_threaded_augmenter import MultiThreadedAugmenter

from typing import Union, Tuple, List

from biom3d.utils import adaptive_imread, get_folds_train_test_df

#---------------------------------------------------------------------------
# random crop and pad with batchgenerator

def located_crop(img, msk, location, crop_shape, margin=np.zeros(3)):
    """Do a crop, forcing the location voxel to be located in the crop.
    """
    img_shape = np.array(img.shape)[1:]
    location = np.array(location)
    crop_shape = np.array(crop_shape)
    margin = np.array(margin)
    
    lower_bound = np.maximum(0,location-crop_shape+margin)
    higher_bound = np.maximum(lower_bound+1,np.minimum(location-margin, img_shape-crop_shape))
    start = np.random.randint(low=lower_bound, high=np.maximum(lower_bound+1,higher_bound))
    end = start+crop_shape
    
    idx = [slice(0,img.shape[0])]+list(slice(s[0], s[1]) for s in zip(start, end))
    idx = tuple(idx)
    
    crop_img = img[idx]
    crop_msk = msk[idx]
    return crop_img, crop_msk

def random_crop(img, msk, crop_shape):
    """
    randomly crop a portion of size prop of the original image size.
    """ 
    img_shape = np.array(img.shape)[1:]
    assert len(img_shape)==len(crop_shape),"[Error] Not the same dimensions! Image shape {}, Crop shape {}".format(img_shape, crop_shape)
    start = np.random.randint(0, np.maximum(1,img_shape-crop_shape))
    end = start+crop_shape
    
    idx = [slice(0,img.shape[0])]+list(slice(s[0], s[1]) for s in zip(start, end))
    idx = tuple(idx)
    
    crop_img = img[idx]
    crop_msk = msk[idx]
    return crop_img, crop_msk

def centered_pad(img, final_size, msk=None):
    """
    centered pad an img and msk to fit the final_size
    """
    final_size = np.array(final_size)
    img_shape = np.array(img.shape[1:])
    
    start = (final_size-np.array(img_shape))//2
    start = start * (start > 0)
    end = final_size-(img_shape+start)
    end = end * (end > 0)
    
    pad = np.append([[0,0]], np.stack((start,end),axis=1), axis=0)
    pad_img = np.pad(img, pad, 'constant', constant_values=0)
    
    if msk is not None:
        pad_msk = np.pad(msk, pad, 'constant', constant_values=0)
        return pad_img, pad_msk
    else: 
        return pad_img

def foreground_crop(img, msk, final_size, fg_margin):
    """Do a foreground crop.
    """
    rnd_label = random.randint(0,msk.shape[0]-1) # choose a random label

    locations = np.argwhere(msk[rnd_label] == 1)

    if locations.size==0: # bug fix when having empty arrays 
        img, msk = random_crop(img, msk, final_size)
    else:
        center=random.choice(locations) # choose a random voxel of this label
        img, msk = located_crop(img, msk, center, final_size, fg_margin)
    return img, msk
    
def random_crop_pad(img, msk, final_size, fg_rate=0.33, fg_margin=np.zeros(3)):
    """
    random crop and pad if needed.
    """
    if type(img)==list: # then batch mode
        imgs, msks = [], []
        for i in range(len(img)):
            img_, msk_ = random_crop_pad(img[i], msk[i], final_size)
            imgs += [img_]
            msks += [msk_]
        return np.array(imgs), np.array(msks) # can convert to array as they should have now all the same shape
    
    # choose if using foreground centrered or random alignement
    force_fg = random.random()
    if fg_rate>0 and force_fg<fg_rate:
        img, msk = foreground_crop(img, msk, final_size, fg_margin)
    else:
        # or random crop
        img, msk = random_crop(img, msk, final_size)
        
    # pad if needed
    if np.any(np.array(img.shape)[1:]-final_size)!=0:
        img, msk = centered_pad(img=img, msk=msk, final_size=final_size)
    return img, msk

class RandomCropAndPadTransform(AbstractTransform):
    def __init__(self, crop_size, fg_rate=0.33, data_key="data", label_key="seg"):
        self.data_key = data_key
        self.label_key = label_key
        self.fg_rate = fg_rate
        self.crop_size = crop_size

    def __call__(self, **data_dict):
        data = data_dict.get(self.data_key)
        seg = data_dict.get(self.label_key)

        data, seg = random_crop_pad(data, seg, self.crop_size, self.fg_rate)

        data_dict[self.data_key] = data
        if seg is not None:
            data_dict[self.label_key] = seg

        return data_dict

#---------------------------------------------------------------------------
# image reader

def imread(img, msk, threeD=True):
    img = adaptive_imread(img)[0]
    msk = adaptive_imread(msk)[0]

    if (threeD and len(img.shape)==3) or (not threeD and len(img.shape)==2):
        img = np.expand_dims(img,0)
    if (threeD and len(msk.shape)==3) or (not threeD and len(msk.shape)==2):
        msk = np.expand_dims(msk,0)

    assert (threeD and len(msk.shape)==4 and len(img.shape)==4) or (not threeD and len(msk.shape)==3 and len(img.shape)==3), "[Error] Your data has the wrong dimension."
    return img, msk

class DataReader(AbstractTransform):
    """Return a data and seg instead of a dictionary.
    """
    def __init__(self, threeD=True, data_key="data", label_key="seg"):
        self.threeD = threeD
        self.data_key = data_key
        self.label_key = label_key
    
    def __call__(self, **data_dict):
        data = data_dict.get(self.data_key)
        seg = data_dict.get(self.label_key)
        
        if type(data)==list:
            for i in range(len(data)):
                data[i], seg[i] = imread(data[i], seg[i])
        else:
            data, seg = imread(data, seg)

        data_dict[self.data_key] = data
        if seg is not None:
            data_dict[self.label_key] = seg
            
        return data_dict
    
#---------------------------------------------------------------------------
# training and validation augmentations


class Convert2DTo3DTransform(AbstractTransform):
    def __init__(self, apply_to_keys: Union[List[str], Tuple[str]] = ('data', 'seg')):
        """
        Reverts Convert3DTo2DTransform by transforming a 4D array (b, c * x, y, z) back to 5D  (b, c, x, y, z)
        """
        self.apply_to_keys = apply_to_keys

    def __call__(self, **data_dict):
        for k in self.apply_to_keys:
            shape_key = f'orig_shape_{k}'
            assert shape_key in data_dict.keys(), f'Did not find key {shape_key} in data_dict. Shitty. ' \
                                                  f'Convert2DTo3DTransform only works in tandem with ' \
                                                  f'Convert3DTo2DTransform and you probably forgot to add ' \
                                                  f'Convert3DTo2DTransform to your pipeline. (Convert3DTo2DTransform ' \
                                                  f'is where the missing key is generated)'
            original_shape = data_dict[shape_key]
            current_shape = data_dict[k].shape
            data_dict[k] = data_dict[k].reshape((original_shape[0], original_shape[1], original_shape[2],
                                                 current_shape[-2], current_shape[-1]))
        return data_dict

class Convert3DTo2DTransform(AbstractTransform):
    def __init__(self, apply_to_keys: Union[List[str], Tuple[str]] = ('data', 'seg')):
        """
        Transforms a 5D array (b, c, x, y, z) to a 4D array (b, c * x, y, z) by overloading the color channel
        """
        self.apply_to_keys = apply_to_keys

    def __call__(self, **data_dict):
        for k in self.apply_to_keys:
            shp = data_dict[k].shape
            assert len(shp) == 5, 'This transform only works on 3D data, so expects 5D tensor (b, c, x, y, z) as input.'
            data_dict[k] = data_dict[k].reshape((shp[0], shp[1] * shp[2], shp[3], shp[4]))
            shape_key = f'orig_shape_{k}'
            assert shape_key not in data_dict.keys(), f'Convert3DTo2DTransform needs to store the original shape. ' \
                                                      f'It does that using the {shape_key} key. That key is ' \
                                                      f'already taken. Bummer.'
            data_dict[shape_key] = shp
        return data_dict

class DictToTuple(AbstractTransform):
    """Return a data and seg instead of a dictionary.
    """
    def __init__(self, data_key="data", label_key="seg"):
        self.data_key = data_key
        self.label_key = label_key
    
    def __call__(self, **data_dict):
        data = data_dict.get(self.data_key)
        seg = data_dict.get(self.label_key)
        if type(seg)==list: seg = seg[0]
        return data, seg
    
class DownsampleSegForDSTransform2(AbstractTransform):
    '''
    data_dict['output_key'] will be a list of segmentations scaled according to ds_scales
    '''
    def __init__(self, ds_scales: Union[List, Tuple],
                 order: int = 0, input_key: str = "seg",
                 output_key: str = "seg", axes: Tuple[int] = None):
        """
        Downscales data_dict[input_key] according to ds_scales. Each entry in ds_scales specified one deep supervision
        output and its resolution relative to the original data, for example 0.25 specifies 1/4 of the original shape.
        ds_scales can also be a tuple of tuples, for example ((1, 1, 1), (0.5, 0.5, 0.5)) to specify the downsampling
        for each axis independently
        """
        self.axes = axes
        self.output_key = output_key
        self.input_key = input_key
        self.order = order
        self.ds_scales = ds_scales

    def __call__(self, **data_dict):
        if self.axes is None:
            axes = list(range(2, len(data_dict[self.input_key].shape)))
        else:
            axes = self.axes

        output = []
        for s in self.ds_scales:
            if not isinstance(s, (tuple, list)):
                s = [s] * len(axes)
            else:
                assert len(s) == len(axes), f'If ds_scales is a tuple for each resolution (one downsampling factor ' \
                                            f'for each axis) then the number of entried in that tuple (here ' \
                                            f'{len(s)}) must be the same as the number of axes (here {len(axes)}).'

            if all([i == 1 for i in s]):
                output.append(data_dict[self.input_key])
            else:
                new_shape = np.array(data_dict[self.input_key].shape).astype(float)
                for i, a in enumerate(axes):
                    new_shape[a] *= s[i]
                new_shape = np.round(new_shape).astype(int)
                out_seg = np.zeros(new_shape, dtype=data_dict[self.input_key].dtype)
                for b in range(data_dict[self.input_key].shape[0]):
                    for c in range(data_dict[self.input_key].shape[1]):
                        out_seg[b, c] = resize_segmentation(data_dict[self.input_key][b, c], new_shape[2:], self.order)
                output.append(out_seg)
        data_dict[self.output_key] = output
        return data_dict
    
def get_training_transforms(aug_patch_size: Union[np.ndarray, Tuple[int]],
                            patch_size: Union[np.ndarray, Tuple[int]],
                            fg_rate: float,
                            rotation_for_DA: dict,
                            deep_supervision_scales: Union[List, Tuple],
                            mirror_axes: Tuple[int, ...],
                            do_dummy_2d_data_aug: bool,
                            order_resampling_data: int = 3,
                            order_resampling_seg: int = 1,
                            border_val_seg: int = -1,
                            use_data_reader: bool = True,
                            ) -> AbstractTransform:
    tr_transforms = []
    
    if use_data_reader:
        tr_transforms.append(DataReader())
    
    tr_transforms.append(RandomCropAndPadTransform(aug_patch_size, fg_rate))
    
    if do_dummy_2d_data_aug:
        ignore_axes = (0,)
        tr_transforms.append(Convert3DTo2DTransform())
        patch_size_spatial = patch_size[1:]
    else:
        patch_size_spatial = patch_size
        ignore_axes = None
    
    tr_transforms.append(SpatialTransform(
        patch_size_spatial, patch_center_dist_from_border=None,
        do_elastic_deform=False, alpha=(0, 0), sigma=(0, 0),
        do_rotation=True, angle_x=rotation_for_DA['x'], angle_y=rotation_for_DA['y'], angle_z=rotation_for_DA['z'],
        p_rot_per_axis=1,  # todo experiment with this
        do_scale=True, scale=(0.7, 1.4),
        border_mode_data="constant", border_cval_data=0, order_data=order_resampling_data,
        border_mode_seg="constant", border_cval_seg=border_val_seg, order_seg=order_resampling_seg,
        random_crop=False,  # random cropping is part of our dataloaders
        p_el_per_sample=0, p_scale_per_sample=0.2, p_rot_per_sample=0.2,
        independent_scale_for_each_axis=False  # todo experiment with this
    ))

    if do_dummy_2d_data_aug:
        tr_transforms.append(Convert2DTo3DTransform())
        
    tr_transforms.append(CenterCropTransform(patch_size, data_key='data', label_key='seg'))

    tr_transforms.append(GaussianNoiseTransform(p_per_sample=0.1))
    tr_transforms.append(GaussianBlurTransform((0.5, 1.), different_sigma_per_channel=True, p_per_sample=0.2,
                                               p_per_channel=0.5))
    tr_transforms.append(BrightnessMultiplicativeTransform(multiplier_range=(0.75, 1.25), p_per_sample=0.15))
    tr_transforms.append(ContrastAugmentationTransform(p_per_sample=0.15))
    tr_transforms.append(SimulateLowResolutionTransform(zoom_range=(0.5, 1), per_channel=True,
                                                        p_per_channel=0.5,
                                                        order_downsample=0, order_upsample=3, p_per_sample=0.25,
                                                        ignore_axes=ignore_axes))
    tr_transforms.append(GammaTransform((0.7, 1.5), True, True, retain_stats=True, p_per_sample=0.1))
    tr_transforms.append(GammaTransform((0.7, 1.5), False, True, retain_stats=True, p_per_sample=0.3))

    if mirror_axes is not None and len(mirror_axes) > 0:
        tr_transforms.append(MirrorTransform(mirror_axes))

    tr_transforms.append(RemoveLabelTransform(-1, 0))

    tr_transforms.append(RenameTransform('seg', 'target', True))

    if deep_supervision_scales is not None:
        tr_transforms.append(DownsampleSegForDSTransform2(deep_supervision_scales, 0, input_key='target',
                                                          output_key='target'))
    tr_transforms.append(NumpyToTensor(['data', 'target'], 'float'))
    tr_transforms.append(DictToTuple('data', 'target'))
    tr_transforms = Compose(tr_transforms)
    return tr_transforms

def get_validation_transforms(patch_size: Union[np.ndarray, Tuple[int]],
                              fg_rate: float,
                              deep_supervision_scales: Union[List, Tuple] = None,
                              use_data_reader: bool = True,
                              ) -> AbstractTransform:
    val_transforms = []

    if use_data_reader:
        val_transforms.append(DataReader())

    val_transforms.append(RandomCropAndPadTransform(patch_size, fg_rate))
    
    val_transforms.append(RemoveLabelTransform(-1, 0))

    val_transforms.append(RenameTransform('seg', 'target', True))


    if deep_supervision_scales is not None:
        val_transforms.append(DownsampleSegForDSTransform2(deep_supervision_scales, 0, input_key='target',
                                                           output_key='target'))

    val_transforms.append(NumpyToTensor(['data', 'target'], 'float'))
    val_transforms.append(DictToTuple('data', 'target'))
    val_transforms = Compose(val_transforms)
    return val_transforms

#---------------------------------------------------------------------------
# dataloader

class BatchGenDataLoader(SlimDataLoaderBase):
    """
    Similar as torchio.SubjectsDataset but can be use with an unlimited amount of steps.
    """
    
    def __init__(
        self,
        img_dir,
        msk_dir,
        batch_size, 
        nbof_steps,
        folds_csv  = None, 
        fold       = 0, 
        val_split  = 0.25,
        train      = True,
        load_data = False,
        
        # batch_generator parameters
        num_threads_in_mt=12, 
    ):
        """
        Parameters
        ----------
        load_data : boolean, default=False
            if True, loads the all dataset into computer memory (faster but more memory expensive). ONLY COMPATIBLE WITH .npy PREPROCESSED IMAGES
        """
        self.img_dir = img_dir
        self.msk_dir = msk_dir

        self.batch_size = batch_size

        self.nbof_steps = nbof_steps

        self.load_data = load_data
        
        # get the training and validation names 
        if folds_csv is not None:
            df = pd.read_csv(folds_csv)
            trainset, testset = get_folds_train_test_df(df, verbose=False)

            self.fold = fold
            
            self.val_imgs = trainset[self.fold]
            del trainset[self.fold]
            self.train_imgs = []
            for i in trainset: self.train_imgs += i

        else: # tmp: validation split = 50% by default
            all_set = os.listdir(img_dir)
            val_split = np.round(val_split * len(all_set)).astype(int)
            if val_split == 0: val_split=1
            self.train_imgs = all_set[val_split:]
            self.val_imgs = all_set[:val_split]
            testset = []
        
        self.train = train
        if self.train:
            print("current fold: {}\n \
            length of the training set: {}\n \
            length of the validation set: {}\n \
            length of the testing set: {}".format(fold, len(self.train_imgs), len(self.val_imgs), len(testset)))

        self.fnames = self.train_imgs if self.train else self.val_imgs

        # print train and validation image names
        print("{} images: {}".format("Training" if self.train else "Validation", self.fnames))
        
        def generate_data(fnames):
            data = []
            for idx in range(len(fnames)):
                # file names
                img = os.path.join(self.img_dir, fnames[idx])
                msk = os.path.join(self.msk_dir, fnames[idx])

                # load img and msks
                if self.load_data:
                    img = adaptive_imread(img)[0]
                    msk = adaptive_imread(msk)[0]
                data += [{'data': img, 'seg': msk}]
                
            return data

        data = generate_data(self.fnames)
        super(BatchGenDataLoader, self).__init__(
            data,
            batch_size,
            num_threads_in_mt,
        )
        self.indices = np.arange(len(self._data))

        self.current_position = 0
        self.was_initialized = False

    def reset(self):
        assert self.indices is not None

        self.current_position = 0

        self.was_initialized = True

    def get_indices(self):
        if not self.was_initialized:
            self.reset()

        indices = np.random.choice(self.indices, self.batch_size, replace=True)
        
        if self.current_position < self.nbof_steps:
            self.current_position += self.number_of_threads_in_multithreaded
            return indices
        else:
            self.was_initialized=False
            raise StopIteration

    def generate_train_batch(self):        
        indices = self.get_indices()
        
        batch_list = [self._data[i] for i in indices]
        batch = {
            'data':[data['data'] for data in batch_list],
            'seg': [data['seg'] for data in batch_list]
        }

        return batch

#---------------------------------------------------------------------------
# multi-threading

def get_patch_size(final_patch_size, rot_x, rot_y, rot_z, scale_range):
    if isinstance(rot_x, (tuple, list)):
        rot_x = max(np.abs(rot_x))
    if isinstance(rot_y, (tuple, list)):
        rot_y = max(np.abs(rot_y))
    if isinstance(rot_z, (tuple, list)):
        rot_z = max(np.abs(rot_z))
    rot_x = min(90 / 360 * 2. * np.pi, rot_x)
    rot_y = min(90 / 360 * 2. * np.pi, rot_y)
    rot_z = min(90 / 360 * 2. * np.pi, rot_z)

    coords = np.array(final_patch_size)
    final_shape = np.copy(coords)
    if len(coords) == 3:
        final_shape = np.max(np.vstack((np.abs(rotate_coords_3d(coords, rot_x, 0, 0)), final_shape)), 0)
        final_shape = np.max(np.vstack((np.abs(rotate_coords_3d(coords, 0, rot_y, 0)), final_shape)), 0)
        final_shape = np.max(np.vstack((np.abs(rotate_coords_3d(coords, 0, 0, rot_z)), final_shape)), 0)
    elif len(coords) == 2:
        final_shape = np.max(np.vstack((np.abs(rotate_coords_2d(coords, rot_x)), final_shape)), 0)
    final_shape /= min(scale_range)
    return final_shape.astype(int)  

def configure_rotation_dummyDA_mirroring_and_inital_patch_size(patch_size):
    """
    This function is stupid and certainly one of the weakest spots of this implementation. Not entirely sure how we can fix it.
    """
    dim = len(patch_size)
    # todo rotation should be defined dynamically based on patch size (more isotropic patch sizes = more rotation)
    if dim == 2:
        do_dummy_2d_data_aug = False
        # todo revisit this parametrization
        if max(patch_size) / min(patch_size) > 1.5:
            rotation_for_DA = {
                'x': (-15. / 360 * 2. * np.pi, 15. / 360 * 2. * np.pi),
                'y': (0, 0),
                'z': (0, 0)
            }
        else:
            rotation_for_DA = {
                'x': (-180. / 360 * 2. * np.pi, 180. / 360 * 2. * np.pi),
                'y': (0, 0),
                'z': (0, 0)
            }
        mirror_axes = (0, 1)
    elif dim == 3:
        # todo this is not ideal. We could also have patch_size (64, 16, 128) in which case a full 180deg 2d rot would be bad
        # order of the axes is determined by spacing, not image size
        do_dummy_2d_data_aug = (max(patch_size) / patch_size[0]) > 3
        if do_dummy_2d_data_aug:
            # why do we rotate 180 deg here all the time? We should also restrict it
            rotation_for_DA = {
                'x': (-180. / 360 * 2. * np.pi, 180. / 360 * 2. * np.pi),
                'y': (0, 0),
                'z': (0, 0)
            }
        else:
            rotation_for_DA = {
                'x': (-30. / 360 * 2. * np.pi, 30. / 360 * 2. * np.pi),
                'y': (-30. / 360 * 2. * np.pi, 30. / 360 * 2. * np.pi),
                'z': (-30. / 360 * 2. * np.pi, 30. / 360 * 2. * np.pi),
            }
        mirror_axes = (0, 1, 2)
    else:
        raise RuntimeError()

    # todo this function is stupid. It doesn't even use the correct scale range (we keep things as they were in the
    #  old nnunet for now)
    initial_patch_size = get_patch_size(patch_size[-dim:],
                                        *rotation_for_DA.values(),
                                        (0.85, 1.25))
    if do_dummy_2d_data_aug:
        initial_patch_size[0] = patch_size[0]

    print(f'do_dummy_2d_data_aug: {do_dummy_2d_data_aug}')

    return rotation_for_DA, do_dummy_2d_data_aug, initial_patch_size, mirror_axes

class MTBatchGenDataLoader(MultiThreadedAugmenter):
    def __init__(
        self,
        img_dir,
        msk_dir,
        patch_size,
        batch_size, 
        nbof_steps,
        folds_csv  = None, 
        fold       = 0, 
        val_split  = 0.25,
        train      = True,
        load_data = False,
        fg_rate     = 0.33,
        num_threads_in_mt=12, 
    ):
        
        gen = BatchGenDataLoader(
            img_dir,
            msk_dir,
            batch_size,
            nbof_steps,
            folds_csv, 
            fold,
            val_split,  
            train,
            load_data, 

            # batch_generator parameters
            num_threads_in_mt,
        )

        self.length = nbof_steps
        
        if train:
            rotation_for_DA, do_dummy_2d_data_aug, aug_patch_size, mirror_axes=configure_rotation_dummyDA_mirroring_and_inital_patch_size(patch_size)
            transform = get_training_transforms(
                                aug_patch_size=aug_patch_size,
                                patch_size=patch_size,
                                fg_rate = fg_rate,
                                rotation_for_DA=rotation_for_DA,
                                # deep_supervision_scales=[[1, 1, 1], [1.0, 0.5, 0.5], [1.0, 0.25, 0.25], [0.5, 0.125, 0.125], [0.25, 0.0625, 0.0625]],
                                deep_supervision_scales=None,
                                mirror_axes=mirror_axes,
                                do_dummy_2d_data_aug=do_dummy_2d_data_aug,
                                use_data_reader=not load_data,
                                )
        else:
            transform = get_validation_transforms(
                                patch_size=patch_size,
                                fg_rate = fg_rate,
                                use_data_reader=not load_data,
                                )
        
        super(MTBatchGenDataLoader, self).__init__(
            gen,
            transform,
            num_threads_in_mt,
            batch_size
        )

    def __len__(self):
        return self.length

#---------------------------------------------------------------------------
