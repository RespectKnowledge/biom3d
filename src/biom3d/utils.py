# ----------------------------------------------------------------------------
# a set of utility functions 
# content:
#  - base class for config file 
#  - read folds from a csv file
#  - create logs and models directories
#  - tif metadata reader and writer
# ----------------------------------------------------------------------------

import numpy as np
from datetime import datetime
from time import time 
import os 
import importlib.util
import sys
import tifffile as tiff
import matplotlib.pyplot as plt
import yaml # pip install pyyaml
from skimage import io
import SimpleITK as sitk
import torchio as tio

# ----------------------------------------------------------------------------
# read folds from a csv file

def get_train_test_df(df, verbose=True):
    """
    Return the train set and the test set
    """
    train_set = np.array(df[df['hold_out']==0].iloc[:,0])
    test_set = np.array(df[df['hold_out']==1].iloc[:,0])
    return train_set, test_set

def get_folds_df(df, verbose=True):
    """
    Return of folds in a list of list
    """
    folds = []
    if df.empty:
        print("[Warning] one of the data DataFrame is empty!")
        return []
    nbof_folds = df['fold'].max()+1
    if verbose:
        print("Number of folds in df: {}".format(nbof_folds))
    
    size_folds = []
    for i in range(nbof_folds):
        folds += [list(df[df['fold']==i].iloc[:,0])]
        size_folds += [len(folds[-1])]
    if verbose:
        print("Size of folds: {}".format(size_folds))
    return folds

def get_folds_train_test_df(df, verbose=True, merge_test=True):
    """
    Return folds from the train set and the test set in a list of list.
    Output: (train_folds, test_folds)
    If merge_test==True then the test folds are merged in one list.
    """
    if verbose:
        print("Training set:")
    train_folds = get_folds_df(df[df['hold_out']==0], verbose)
    
    if verbose:
        print("Testing set:")
    test_folds = get_folds_df(df[df['hold_out']==1], verbose)
    
    if merge_test:
        test_folds_merged = []
        for i in range(len(test_folds)):
            test_folds_merged += test_folds[i]
        test_folds = test_folds_merged
    return train_folds, test_folds

def get_splits_train_val_test(df):
    """
    the splits contains [100%,50%,25%,10%,5%,2%,the rest] of the dataset
    return the train set as a list of list,
    the val and test set as lists
    """
    nbof_splits = df['split'].max()+1
    valset = list(df[(df['split']==-1)*(df['fold']==0)*(df['hold_out']==0)]['filename'])
    testset = list(df[(df['hold_out']==1)]['filename'])
    train_splits = []
    for i in range(nbof_splits):
        train_splits += [list(df[(df['split']==i)*(df['fold']!=0)*(df['hold_out']==0)].iloc[:,0])]
    # adds the whole dataset in the begging of the train_splits list
    train_splits = [list(df[(df['fold']!=0)*(df['hold_out']==0)].iloc[:,0])] + train_splits
    return train_splits, valset, testset

def get_splits_train_val_test_overlapping(df):
    """
    CAREFUL: works only if the splits contains [1/(2**0), 1/(2**1), ..., 1/(2**n), 1/(2**n)] of the training dataset 
    the splits contains of the dataset.
    "overlapping" indicates that every smaller set is contained into all bigger sets.
    return the train set as a list of list,
    the val and test set as lists
    """
    nbof_splits = df['split'].max()+1
    valset = list(df[(df['split']==-1)*(df['fold']==0)*(df['hold_out']==0)]['filename'])
    testset = list(df[(df['hold_out']==1)]['filename'])
    train_splits = []
    for i in range(nbof_splits):
        train_splits += [list(df[(df['split']>=i)*(df['fold']!=0)*(df['hold_out']==0)].iloc[:,0])]
        
    # adds the last set 
    train_splits += [list(df[(df['split']==(nbof_splits-1))*(df['fold']!=0)*(df['hold_out']==0)].iloc[:,0])]
    return train_splits, valset, testset

# ----------------------------------------------------------------------------
# create logs and models directories

def create_save_dirs(log_dir, desc, dir_names=['model', 'logs', 'images'], return_base_dir=False):
    """
    Creates saving folders. 

    Arguments:
        dir_names: a list of name of the desired folders.
                   e.g.: ['images','cpkt','summary']
    
    Returns:
        list_dirs: a list of path of the corresponding folders.
    """
    list_dirs = []
    current_time = datetime.now().strftime("%Y%m%d-%H%M%S")
    base_dir = current_time + '-' + desc
    base_dir = os.path.join(log_dir, base_dir)
    for name in dir_names:
        list_dirs += [os.path.join(base_dir, name)]
        if not os.path.exists(list_dirs[-1]):
            os.makedirs(list_dirs[-1])
    if return_base_dir:
        return [base_dir] + list_dirs
    else:
        return list_dirs

# ----------------------------------------------------------------------------
# image readers and savers

def sitk_imread(img_path):
    """
    image reader for nii.gz files
    """
    img = sitk.ReadImage(img_path)
    img_np = sitk.GetArrayFromImage(img)
    return img_np, np.array(img.GetSpacing())

def adaptive_imread(img_path):
    """
    use skimage imread or sitk imread depending on the file extension:
    .tif --> skimage.io.imread
    .nii.gz --> SimpleITK.imread
    """
    extension = img_path[img_path.rfind('.'):]
    if extension == ".tif":
        return io.imread(img_path), []
    elif extension == ".npy":
        return np.load(img_path), []
    else:
        return sitk_imread(img_path)

def sitk_imsave(img_path, img, spacing=(1,1,1)):
    """
    image saver for nii gz files
    """
    img_out = sitk.GetImageFromArray(img)
    img_out.SetSpacing(spacing)
    sitk.WriteImage(img_out, img_path)

def adaptive_imsave(img_path, img, spacing=(1,1,1)):
    """Adaptive image saving. Use tifffile for `.tif`, use numpy for `.npy` and use SimpleITK for other format. 

    Parameters
    ----------
        img_path : str
            Path to the output file.
        img : numpy.ndarray
            Image array.
        spacing : tuple, default=(1,1,1)
            Optional spacing of the image. Only used with the SimpleITK library.
    """
    extension = img_path[img_path.rfind('.'):]
    if extension == ".tif":
        tiff.imwrite(img_path, img, compression=('zlib', 1))
    elif extension == ".npy":
        np.save(img_path, img)
    else:
        sitk_imsave(img_path, img, spacing)

# ----------------------------------------------------------------------------
# tif metadata reader and writer

def tif_read_meta(tif_path, display=False):
    """
    read the metadata of a tif file and stores them in a python dict.
    if there is a 'ImageDescription' tag, it transforms it as a dictionary
    """
    meta = {}
    with tiff.TiffFile(tif_path) as tif:
        for page in tif.pages:
            for tag in page.tags:
                tag_name, tag_value = tag.name, tag.value
                if display: print(tag.name, tag.code, tag.dtype, tag.count, tag.value)

                # below; fix storage problem for ImageDescription tag
                if tag_name == 'ImageDescription': 
                    list_desc = tag_value.split('\n')
                    dict_desc = {}
                    for idx, elm in enumerate(list_desc):
                        split = elm.split('=')
                        dict_desc[split[0]] = split[1]
                    meta[tag_name] = dict_desc
                else:
                    meta[tag_name] = tag_value
            break # just check the first image
    return meta

def tif_write_meta(data,meta,out_path):
    """
    write data and metadata in 'out_path'
    """
    out_meta = {
        'spacing':float(meta['ImageDescription']['spacing']),
        'unit':meta['ImageDescription']['unit'],
        'axes':'ZYX',
    }
    
    extratags = []
    
    tiff.imwrite(
        out_path,
        data=data,
        resolution=(meta['XResolution'],meta['YResolution']),
        metadata=out_meta,
        extratags=extratags,
        imagej=True,
    )

def tif_copy_meta(in_path1, in_path2, out_path):
    """
    store (metadata of in_path1 + data of in_path2) in out_path
    """
    in_meta = tif_read_meta(in_path1)
    data = tiff.imread(in_path2)
    tif_write_meta(data, in_meta, out_path)

def tif_get_spacing(path):
    """
    get the image spacing stored in the metadata file.
    """
    img_meta = tif_read_meta(path)

    xres = (img_meta["XResolution"][1]/img_meta["XResolution"][0])*1e-6
    yres = (img_meta["YResolution"][1]/img_meta["YResolution"][0])*1e-6
    zres = float(img_meta["ImageDescription"]["spacing"])*1e-6
    max_dim = min([xres,yres,zres])
    xres = max_dim / xres
    yres = max_dim / yres
    zres = max_dim / zres
    return (xres, yres, zres)

# ----------------------------------------------------------------------------
# 3d viewer

def display_voxels(image, xlim, ylim, zlim, save=False):
    """
    plot using matplotlib a 3d volume from a 3d image
    """
    fig = plt.figure()
    ax = fig.add_subplot(projection='3d')
    ax.voxels(image)
    
    ax.set_xlim(xlim[0], xlim[1])  
    ax.set_ylim(ylim[0], ylim[1])  
    ax.set_zlim(zlim[0], zlim[1])
    
    plt.tight_layout()
    plt.savefig('voxel.png') if save else plt.show() 

def display_mesh(mesh, xlim, ylim, zlim, save=False):
    """
    plot using matplotlib a 3d volume from a 3d mesh
    """
    fig = plt.figure()
    ax = fig.add_subplot(projection='3d')
    ax.add_collection3d(mesh)
    
    ax.set_xlim(xlim[0], xlim[1])  
    ax.set_ylim(ylim[0], ylim[1])  
    ax.set_zlim(zlim[0], zlim[1])
    
    plt.tight_layout()
    plt.savefig('mesh.png') if save else plt.show() 

def napari_viewer(img, pred):
    import napari
    viewer = napari.view_image(img, name='original')
    viewer.add_image(pred, name='pred')
    viewer.layers['pred'].opacity=0.5
    viewer.layers['pred'].colormap='red'
    napari.run()

# ----------------------------------------------------------------------------
# os utils

def abs_path(root, listdir_):
    listdir = listdir_.copy()
    for i in range(len(listdir)):
        listdir[i] = os.path.join(root, listdir[i])
    return listdir

def abs_listdir(path):
    return abs_path(path, os.listdir(path))

# ----------------------------------------------------------------------------
# preprocess utils
# from the median image shape predict the size of the patch, the pool, the batch 

def single_patch_pool(dim, size_limit=7):
    """
    divide by two the dim number until obtaining a number lower than 7
    then np.ceil this number
    then multiply multiple times by two this number to obtain the patch size
    """
    pool = 0
    while dim > size_limit:
        dim /= 2
        pool += 1
    patch = np.round(dim)
    patch = patch.astype(int)*(2**pool)
    return patch, pool

def find_patch_pool_batch(dims, max_dims=(128,128,128), max_pool=5, epsilon=1e-3):
    """
    take the median size as input, determine the patch size and the number of pool
    with "single_patch_pool" function for each dimension and 
    assert that the final dimension size is smaller than max_dims.prod().
    """
    # transform tuples into arrays
    dims = np.array(dims)
    max_dims = np.array(max_dims)
    
    # divides by a 1+epsilon until reaching a sufficiently small resolution
    while dims.prod() > max_dims.prod():
        dims = dims / (1+epsilon)
    dims = dims.astype(int)
    
    # compute patch and pool for all dims
    patch_pool = np.array([single_patch_pool(m) for m in dims])
    patch = patch_pool[:,0]
    pool = patch_pool[:,1]
    
    # assert the final size is smaller than max_dims
    while patch.prod()>max_dims.prod():
        patch = patch - np.array([32,32,32])*(patch>max_dims) # removing multiples of 32
    pool = np.where(pool > max_pool, max_pool, pool)
    
    # batch_size
    batch = 2
    while batch*patch.prod() <= 2*max_dims.prod():
        batch += 1
    if batch*patch.prod() > 2*max_dims.prod():
        batch -= 1
    return patch, pool, batch

def convert_num_pools(num_pools):
    """
    Set adaptive number of pools
        for example: convert [3,5,5] into [[1 1 1],[1 2 2],[2 2 2],[2 2 2],[2 2 2],[1 2 2]]
    """
    max_pool = max(num_pools)
    strides = []
    for i in range(len(num_pools)):
        st = np.ones(max_pool)
        num_zeros = max_pool-num_pools[i]
        for j in range(num_zeros):
            st[j]=0
        # st=np.roll(st,-num_zeros//2)
        strides += [st]
    strides = np.array(strides).astype(int).T+1
    # kernels = (strides*3//2).tolist()
    strides = strides.tolist()
    return strides

# ----------------------------------------------------------------------------
# data augmentation utils

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
    if msk is not None: pad_msk = np.pad(msk, pad, 'constant', constant_values=0)
    
    if msk is not None:
        return pad_img, pad_msk
    else: 
        return pad_img

class RandomCropResize:
    """
    SmartPatch
    Randomly crop and resize the images to a certain crop_shape.
    The global_crop_resize method performs a random crop and resize.
    The local_crop_resize method performs a random crop and resize making sure that the crop 
    is overlapping (to a certain extent, defined by the min_overlap parameter) with the global
    crop previously performed. 
    """
    def __init__(
        self,
        local_crop_shape,
        global_crop_shape,
        min_overlap,
        global_crop_scale=1.0,
        global_crop_min_shape_scale=1.0,
        ):
        """
        Parameters
        ----------
        global_crop_shape : list or tuple of size == 3
            Minimal crop size
        global_crop_scale : float, default=1.0
            Value between 0 and 1. Factor multiplying (img_shape - global_crop_min_shape) and added to the global_crop_min_shape. A value of 1 means that the maximum shape of the global crop will be the image shape. A value of 0 means that the maximum value will be the global_crop_min_shape. 
        global_crop_min_shape_factor : float, default=1.0
            (DEPRECATED?) Factor multiplying the minimal global_crop_shape, 1.0 is a good default
        
        """
        
        self.local_crop_shape = np.array(local_crop_shape)
        self.global_crop_shape = np.array(global_crop_shape)
        self.global_crop_scale = np.array(global_crop_scale)
        self.global_crop_min_shape_scale = np.array(global_crop_min_shape_scale)
        self.alpha = 1  - min_overlap
        
        # internal arguments
        self.global_crop_center = None
        
    def global_crop_resize(self, img, msk=None):
        img_shape = np.array(img.shape)[1:]
        
        # determine crop shape
        min_crop_shape = np.round(self.global_crop_shape * self.global_crop_min_shape_scale).astype(int)
        min_crop_shape = np.minimum(min_crop_shape, img_shape)
        crop_shape = np.random.randint(min_crop_shape, (img_shape-min_crop_shape)*self.global_crop_scale+min_crop_shape+1)
        
        # determine crop coordinates
        rand_start = np.random.randint(0, np.maximum(1,img_shape-crop_shape))
        rand_end = crop_shape+rand_start
        
        self.global_crop_center = (rand_end-rand_start)//2 + rand_start
        
        # crop
        crop_img = img[:,
                        rand_start[0]:rand_end[0], 
                        rand_start[1]:rand_end[1], 
                        rand_start[2]:rand_end[2]]
        
        if msk is not None:
            crop_msk = msk[:,
                            rand_start[0]:rand_end[0], 
                            rand_start[1]:rand_end[1], 
                            rand_start[2]:rand_end[2]]
    
        # temp: resize must be done!
        if not np.array_equal(crop_img.shape[1:], self.global_crop_shape):
            if msk is not None:
                sub = tio.Subject(img=tio.ScalarImage(tensor=crop_img), msk=tio.LabelMap(tensor=crop_msk))
                sub = tio.Resize(self.global_crop_shape)(sub)
                crop_img, crop_msk = sub.img.tensor, sub.msk.tensor
            else:
                crop_img = tio.Resize(self.global_crop_shape)(crop_img)
        
        # returns
        if msk is not None:
            return crop_img, crop_msk
        else:
            return crop_img

    def local_crop_pad(self, img, msk=None):
        """
        global_crop_resize must be called at least once before calling local_crop_pad
        """
        assert self.global_crop_center is not None, "Error! self.global_crop_resize must be called once before self.local_crop_pad."
        
        img_shape = np.array(img.shape)[1:]
        crop_shape = self.local_crop_shape
        
        # determine crop coordinates
        # we make sure that the crop shape overlap with the global crop shape by at least min_overlap
        centers_max_dist = np.round(crop_shape * self.alpha).astype(np.uint8) + (self.global_crop_shape-crop_shape)//2
        local_center_low = np.maximum(crop_shape//2, self.global_crop_center-centers_max_dist)
        local_center_high = np.minimum(img_shape - crop_shape//2, self.global_crop_center+centers_max_dist)
        local_center_high = np.maximum(local_center_high, local_center_low+1)

        local_crop_center = np.random.randint(low=local_center_low, high=local_center_high)
        
        # local
        start = local_crop_center - (self.local_crop_shape//2)
        start = np.maximum(0,start)
        end = start + self.local_crop_shape
        
        crop_img = img[:,
                    start[0]:end[0], 
                    start[1]:end[1], 
                    start[2]:end[2]]
        
        if msk is not None:
            crop_msk = msk[:,
                        start[0]:end[0], 
                        start[1]:end[1], 
                        start[2]:end[2]]
        
        # pad if needed
        if not np.array_equal(crop_img.shape[1:], self.local_crop_shape):
            if msk is not None:
                crop_img, crop_msk = centered_pad(img=crop_img, final_size=self.local_crop_shape, msk=crop_msk)
            else:
                crop_img = centered_pad(img=crop_img, final_size=self.local_crop_shape)
        
        # returns
        if msk is not None:
            return crop_img, crop_msk
        else:
            return crop_img

    def local_crop_resize(self, img, msk=None):
        """
        global_crop_resize must be called at least once before calling local_crop_resize
        """
        assert self.global_crop_center is not None, "Error! self.global_crop_resize must be called once before self.local_crop_resize."

        img_shape = np.array(img.shape)[1:]

        # determine crop shape
        crop_shape = np.random.randint(self.local_crop_scale[0] * img_shape, self.local_crop_scale[1] * img_shape+1)
        
        # determine crop coordinates
        # we make sure that the crop shape overlap with the global crop shape by at least min_overlap
        centers_max_dist = np.round(crop_shape * self.alpha).astype(np.uint8) + (self.global_crop_shape-crop_shape)//2
        local_center_low = np.maximum(crop_shape//2, self.global_crop_center-centers_max_dist)
        local_center_high = np.minimum(img_shape - crop_shape//2, self.global_crop_center+centers_max_dist)
        local_center_high = np.maximum(local_center_high, local_center_low+1)

        local_crop_center = np.random.randint(low=local_center_low, high=local_center_high)
        
        start = local_crop_center - (self.local_crop_shape//2)
        start = np.maximum(0,start)
        end = start + self.local_crop_shape
        
        crop_img = img[:,
                    start[0]:end[0], 
                    start[1]:end[1], 
                    start[2]:end[2]]
        
        if msk is not None:
            crop_msk = msk[:,
                        start[0]:end[0], 
                        start[1]:end[1], 
                        start[2]:end[2]]
        
        # resize if needed
        if not np.array_equal(crop_img.shape[1:], self.local_crop_shape):
            if msk is not None:
                sub = tio.Subject(img=tio.ScalarImage(tensor=crop_img), msk=tio.LabelMap(tensor=crop_msk))
                sub = tio.Resize(self.global_crop_shape)(sub)
                crop_img, crop_msk = sub.img.tensor, sub.msk.tensor
            else:
                crop_img = tio.Resize(self.global_crop_shape)(crop_img)
        
        # returns
        if msk is not None:
            return crop_img, crop_msk
        else:
            return crop_img

# ----------------------------------------------------------------------------
# config utils
# Convenience class that behaves exactly like dict(), but allows accessing
# the keys and values using the attribute syntax, i.e., "mydict.key = value".
# Author: Terro Keras (progressive_growing_of_gans)

class Dict(dict):
    def __init__(self, *args, **kwargs): super().__init__(*args, **kwargs)
    def __getattr__(self, name): return self[name]
    def __setattr__(self, name, value): self[name] = value
    def __delattr__(self, name): del self[name]

def Dict_to_dict(cfg):
    """
    transform a Dict into a dict
    """
    ty = type(cfg)
    cfg = dict(cfg)
    for k,i in cfg.items():
        if type(i)==ty:
            cfg[k] = Dict_to_dict(cfg[k])
    return cfg

def dict_to_Dict(cfg):
    """
    transform a Dict into a dict
    """
    ty = type(cfg)
    cfg = Dict(cfg)
    for k,i in cfg.items():
        if type(i)==ty:
            cfg[k] = dict_to_Dict(cfg[k])
    return cfg

def save_config(path, cfg):
    """
    save a configuration in a yaml file.
    path must thus contains a yaml extension.
    example: path='logs/test.yaml'
    """
    cfg = Dict_to_dict(cfg)
    with open(path, "w") as f:
        yaml.dump(cfg, f, sort_keys=False)
    
def load_yaml_config(path):
    """
    load a yaml stored with the self.save method.
    """
    return dict_to_Dict(yaml.load(open(path),Loader=yaml.FullLoader))

def nested_dict_pairs_iterator(dic):
    ''' This function accepts a nested dictionary as argument
        and iterate over all values of nested dictionaries
        stolen from: https://thispointer.com/python-how-to-iterate-over-nested-dictionary-dict-of-dicts/ 
    '''
    # Iterate over all key-value pairs of dict argument
    for key, value in dic.items():
        # Check if value is of dict type
        if isinstance(value, dict) or isinstance(value, Dict):
            # If value is dict then iterate over all its values
            for pair in  nested_dict_pairs_iterator(value):
                yield [key, *pair]
        else:
            # If value is not dict type then yield the value
            yield [key, value]

def nested_dict_change_value(dic, key, value):
    """
    Change all value with a given key from a nested dictionary.
    """
    # Loop through all key-value pairs of a nested dictionary and change the value 
    for pairs in nested_dict_pairs_iterator(dic):
        if key in pairs:
            save = dic[pairs[0]]; i=1
            while i < len(pairs) and pairs[i]!=key:
                save = save[pairs[i]]; i+=1
            save[key] = value
    return dic

def load_python_config(config_path):
    """Return the configuration dictionary given the path of the configuration file.
    The configuration file is in Python format.
    
    Adapted from: https://stackoverflow.com/questions/67631/how-can-i-import-a-module-dynamically-given-the-full-path 
    
    Parameters
    ----------
    config_path : str
        Path of the configuration file. Should have the '.py' extension.
    
    Returns
    -------
    cfg : biom3d.utils.Dict
        Dictionary of the config.
    """
    spec = importlib.util.spec_from_file_location("config", config_path)
    config = importlib.util.module_from_spec(spec)
    sys.modules["config"] = config
    spec.loader.exec_module(config)
    return config.CONFIG

# ----------------------------------------------------------------------------
# postprocessing utils

from skimage import measure
def dist_vec(v1,v2):
    """
    euclidean distance between two vectors (np.array)
    """
    v = v2-v1
    return np.sqrt(np.sum(v*v))

def center(labels, idx):
    """
    return the barycenter of the pixels of label = idx
    """
    # dim = labels.shape
    
    # matrix of coordinate with the same size as labels
    # x, y, z = np.meshgrid(np.arange(dim[1]),np.arange(dim[0]), np.arange(dim[2]))
    # out = np.stack((y,x,z))
    # out = np.transpose(out, axes=(1,2,3,0))
    
    # extract the barycenter
    # return np.mean(out[labels==idx], axis=0)
    return np.mean(np.argwhere(labels == idx), axis=0)

def closest(labels, num):
    """
    return the index of the object the closest to the center of the image.
    num: number of label in the image (background does not count)
    """
    labels_center = np.array(labels.shape)/2
    centers = [center(labels,idx+1) for idx in range(num)]
    dist = [dist_vec(labels_center,c) for c in centers]
    return np.argmin(dist)+1

def keep_center_only(msk):
    """
    return mask (msk) with only the connected component that is the closest 
    to the center of the image.
    """
    labels, num = measure.label(msk, background=0, return_num=True)
    close_idx = closest(labels,num)
    return (labels==close_idx).astype(msk.dtype)*255

def volumes(labels):
    """
    returns the volumes of all the labels in the image
    """
    # return [((labels==idx).astype(int)).sum() for idx in np.unique(labels)]
    return np.unique(labels, return_counts=True)[1]

def keep_big_volumes(msk, thres_rate=0.1):
    """
    Return the mask (msk) with less labels/volumes. Select only the biggest volumes with
    the following strategy: minimum_volume = thres_rate * np.sum(np.square(vol))/np.sum(vol)
    This computation could be seen as the expected volume if the variable volume follows the 
    probability distribution: p(vol) = vol/np.sum(vol) 
    """
    # transform image to label
    labels = measure.label(msk, background=0)

    # compute the volume
    unq_labels,vol = np.unique(labels, return_counts=True)

    # remove bg
    unq_labels = unq_labels[1:]
    vol = vol[1:]

    # compute the expected volume
    expected_vol = np.sum(np.square(vol))/np.sum(vol)
    min_vol = expected_vol * thres_rate

    # keep only the labels for which the volume is big enough
    unq_labels = unq_labels[vol > min_vol]

    # compile the selected volumes into 1 image
    s = (labels==unq_labels[0])
    for i in range(1,len(unq_labels)):
        s += (labels==unq_labels[i])

    return s

def keep_biggest_volume_centered(msk):
    """
    return mask (msk) with only the connected component that is the closest 
    to the center of the image if its volumes is not too small ohterwise returns
    the biggest object (different from the background).
    (too small meaning that its volumes shouldn't smaller than half of the biggest one)
    the final mask intensities are either 0 or msk.max()
    """
    labels, num = measure.label(msk, background=0, return_num=True)
    close_idx = closest(labels,num)
    vol = volumes(labels)
    relative_vol = [vol[close_idx]/vol[idx] for idx in range(1,len(vol))]
    min_rel_vol = np.min(relative_vol)
    if min_rel_vol < 0.5:
        close_idx = np.argmin(relative_vol)+1
    return (labels==close_idx).astype(msk.dtype)*msk.max()

# ----------------------------------------------------------------------------
# test utils

def one_hot(values, num_classes=None):
    """
    transform the values np.array into a one_hot encoded
    """
    if num_classes==None: n_values = np.max(values) + 1
    else: n_values = num_classes
        
    # WARNING! potential bug if we have 255 label
    # this function normalize the values to 0,1 if it founds that the maximum of the values if 255
    if values.max()==255: values = (values / 255).astype(np.int64) 
    
    # re-order values if needed
    # for examples if unique values are [2,124,178,250] then they will be changed to [0,1,2,3]
    uni, inv = np.unique(values, return_inverse=True)
    if np.array_equal(uni, np.arange(len(uni))):
        values = np.arange(len(uni))[inv].reshape(values.shape)
        
    out = np.eye(n_values)[values]
    return np.moveaxis(out, -1, 0).astype(np.int64)

def one_hot_fast(values, num_classes=None):
    """
    transform the 'values' array into a one_hot encoded one

    Warning ! If the number of unique values in the input array is lower than the number of classes, then it will consider that the array values are all between zero and `num_classes`. If one value is greater than `num_classes`, then it will add missing values systematically after the maximum value, which could not be the expected behavior. 
    """
    # get unique values
    uni = np.sort(np.unique(values)).astype(np.uint8)

    if num_classes==None: 
        n_values = len(uni)
    else: 
        n_values = num_classes
    
        # if the expected number of class is two then apply a threshold
        if n_values==2 and (len(uni)>2 or uni.max()>1):
            print("[Warning] The number of expected values is 2 but the maximum value is higher than 1. Threshold will be applied.")
            values = (values>uni[0]).astype(np.uint8)
            uni = np.array([0,1]).astype(np.uint8)
        
        # add values if uni is incomplete
        if len(uni)<n_values: 
            # if the maximum value of the array is greater than n_value, it might be an error but still, we add values in the end.
            if values.max() >= n_values:
                print("[Warning] The maximum values in the array is greater than the provided number of classes, this might be unexpected and might cause issues.")
                while len(uni)<n_values:
                    uni = np.append(uni, np.uint8(uni[-1]+1))
            # add missing values in the array by considering that each values are in 0 and n_value
            else:
                uni = np.arange(0,n_values).astype(np.uint8)
        
    # create the one-hot encoded matrix
    out = np.zeros((n_values, *values.shape), dtype=np.uint8)
    for i in range(n_values):
        out[i] = (values==uni[i]).astype(np.uint8)
    return out

# metric definition
def iou(inputs, targets, smooth=1):
    inter = (inputs & targets).sum()
    union = (inputs | targets).sum()
    return (inter+smooth)/(union+smooth)

def dice(inputs, targets, smooth=1, axis=(-3,-2,-1)):   
    """Dice score between inputs and targets.
    """
    inter = (inputs & targets).sum(axis=axis)   
    dice = (2.*inter + smooth)/(inputs.sum(axis=axis) + targets.sum(axis=axis) + smooth)  
    return dice.mean()

def versus_one(fct, in_path, tg_path, num_classes, single_class=None):
    """
    comparison function between in_path image and tg_path and using the criterion defined by fct
    """
    img1,_ = adaptive_imread(in_path)
    print("input path",in_path)
    if len(img1.shape)==3:
        img1 = one_hot_fast(img1, num_classes)[1:,...]
    if single_class is not None:
        img1 = img1[single_class,...]
    img1 = (img1 > 0).astype(int)
    
    img2,_ = adaptive_imread(tg_path)
    print("target path",tg_path)
    if len(img2.shape)==3:
        img2 = one_hot_fast(img2, num_classes)[1:,...]
    if single_class is not None:
        img2 = img2[single_class,...]
    img2 = (img2 > 0).astype(int)
    
    # remove background if needed
    if img1.shape[0]==(img2.shape[0]+1):
        img1 = img1[1:]
    if img2.shape[0]==(img1.shape[0]+1):
        img2 = img2[1:]
    
    if sum(img1.shape)!=sum(img2.shape):
        print("bug:sum(img1.shape)!=sum(img2.shape):")
        print("img1.shape", img1.shape)
        print("img2.shape", img2.shape)
        return
    out = fct(img1, img2)
    return out

# ----------------------------------------------------------------------------
# time utils

class Time:
    def __init__(self, name=None):
        self.name=name
        self.reset()
    
    def reset(self):
        print("Count has been reset!")
        self.start_time = time()
        self.count = 0
    
    def get(self):
        self.count += 1
        return time()-self.start_time
    
    def __str__(self):
        self.count += 1
        out = time() - self.start_time
        self.start_time=time()
        return "[DEBUG] name: {}, count: {}, time: {} seconds".format(self.name, self.count, out)

# ----------------------------------------------------------------------------
