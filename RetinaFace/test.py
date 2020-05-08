from __future__ import print_function
import os
import argparse
import torch
import torch.backends.cudnn as cudnn
import numpy as np
from prior_box import PriorBox
import cv2
from models.retinaface import RetinaFace
from utils.box_utils import *
import time
import sys

sys.path.append(os.path.realpath(__file__).replace(__file__, ''))

parser = argparse.ArgumentParser(description='Retinaface')

parser.add_argument('-m', '--trained_model', default = 'weights/mobilenet0.25_epoch_60.pth',
                    type=str, help='Trained state_dict file path to open')

parser.add_argument('--network', default='mobile0.25', help='Backbone network mobile0.25 or resnet50')
parser.add_argument('--cpu', action="store_true", default=False, help='Use cpu inference')
parser.add_argument('--confidence_threshold', default=0.02, type=float, help='confidence_threshold')
parser.add_argument('--top_k', default=5000, type=int, help='top_k')
parser.add_argument('--nms_threshold', default=0.4, type=float, help='nms_threshold')
parser.add_argument('--keep_top_k', default=750, type=int, help='keep_top_k')
parser.add_argument('-s', '--save_image', action="store_true", default=True, help='show detection results')
parser.add_argument('--vis_thres', default=0.75, type=float, help='visualization_threshold')
args = parser.parse_args()


def check_keys(model, pretrained_state_dict):
    ckpt_keys = set(pretrained_state_dict.keys())
    model_keys = set(model.state_dict().keys())
    used_pretrained_keys = model_keys & ckpt_keys
    unused_pretrained_keys = ckpt_keys - model_keys
    missing_keys = model_keys - ckpt_keys
    print('Missing keys:{}'.format(len(missing_keys)))
    print('Unused checkpoint keys:{}'.format(len(unused_pretrained_keys)))
    print('Used keys:{}'.format(len(used_pretrained_keys)))
    assert len(used_pretrained_keys) > 0, 'load NONE from pretrained checkpoint'
    return True


def remove_prefix(state_dict, prefix):
    ''' Old style model is stored with all names of parameters sharing common prefix 'module.' '''
    print('remove prefix \'{}\''.format(prefix))
    f = lambda x: x.split(prefix, 1)[-1] if x.startswith(prefix) else x
    return {f(key): value for key, value in state_dict.items()}


def load_model(model, pretrained_path, load_to_cpu):
    print('Loading pretrained model from {}'.format(pretrained_path))
    if load_to_cpu:
        pretrained_dict = torch.load(pretrained_path, map_location=lambda storage, loc: storage)
    else:
        device = torch.cuda.current_device()
        pretrained_dict = torch.load(pretrained_path, map_location=lambda storage, loc: storage.cuda(device))
    if "state_dict" in pretrained_dict.keys():
        pretrained_dict = remove_prefix(pretrained_dict['state_dict'], 'module.')
    else:
        pretrained_dict = remove_prefix(pretrained_dict, 'module.')
    check_keys(model, pretrained_dict)
    model.load_state_dict(pretrained_dict, strict=False)
    return model


if __name__ == '__main__':
    torch.set_grad_enabled(False)
    cfg = None


    cfg = {
    'name': 'mobilenet0.25',
    'min_sizes': [[16, 32], [64, 128], [256, 512]],
    'steps': [8, 16, 32],
    'variance': [0.1, 0.2],
    'clip': False,
    'loc_weight': 2.0,
    'gpu_train': True,
    'batch_size': 1,
    'ngpu': 1,
    'image_size': 640,
    'pretrain': True,
    'return_layers': {'stage1': 1, 'stage2': 2, 'stage3': 3},
    'in_channel': 32,
    'out_channel': 64
    }

    root_path = os.path.realpath(__file__).replace("test.py", "")
    model_path = root_path + args.trained_model

    # net and model
    net = RetinaFace(cfg=cfg, phase = 'test')
    net = load_model(net, model_path, args.cpu)
    net.eval()
    print('Finished loading model!')
    print(net)
    cudnn.benchmark = True
    device = torch.device("cpu" if args.cpu else "cuda")
    net = net.to(device)

    resize = 1

    ims = os.listdir('/home/test_images')
    im_path = [os.path.join('/home/test_images', im) for im in ims]

    # testing begin
    for image_path in im_path:
        #image_path = "/root/face_mask_lmks_detection/test.jpg"
        img_raw = cv2.imread(image_path, cv2.IMREAD_COLOR)

        img = np.float32(img_raw)

        im_height, im_width, _ = img.shape
        scale = torch.Tensor([img.shape[1], img.shape[0], img.shape[1], img.shape[0]]) # w h w h
        img -= (104, 117, 123)
        img = img.transpose(2, 0, 1)
        img = torch.from_numpy(img).unsqueeze(0)
        img = img.to(device)
        scale = scale.to(device)

        tic = time.time()
        loc, conf, landms = net(img)  # forward pass
        print('net forward time: {:.4f}'.format(time.time() - tic))

        priorbox = PriorBox(cfg, image_size=(im_height, im_width))
        priors = priorbox.forward()
        priors = priors.to(device)
        prior_data = priors.data
        
        boxes = decode(loc.data.squeeze(0), prior_data, cfg['variance'])
        boxes = boxes * scale / resize
        boxes = boxes.cpu().numpy()

        scores = conf.squeeze(0).data.cpu().numpy()[:, 1:] # conf : batch, num anchors, 3

        landms = decode_landm(landms.data.squeeze(0), prior_data, cfg['variance'])
        scale1 = torch.Tensor([img.shape[3], img.shape[2], img.shape[3], img.shape[2],
                               img.shape[3], img.shape[2], img.shape[3], img.shape[2],
                               img.shape[3], img.shape[2]])
        
        scale1 = scale1.to(device)
        landms = landms * scale1 / resize
        landms = landms.cpu().numpy()

        # ignore low scores
        inds = np.where(scores > args.confidence_threshold)[0]
        boxes = boxes[inds]
        landms = landms[inds]
        scores = scores[inds] # 1, num_anchors, 2

        # # keep top-K before NMS, cos there are 2 different label, split them then concat
        # tem_scores = []
        # tem_boexes = []
        # tem_landms = []

        # for i in range(scores.shape[-1]):
        #     per_cls_scores = scores[..., i]
        #     per_cls_boxes = boxes[..., i]
        #     pre_cls_landms = landms[..., i]

        #     # keep top-K before NMS
        #     order = per_cls_scores.argsort()[::-1][:args.top_k]
        #     per_cls_boxes = per_cls_boxes[order]
        #     pre_cls_landms = pre_cls_landms[order]
        #     per_cls_scores = per_cls_scores[order]

        #     tem_scores.append(per_cls_scores)
        #     tem_boexes.append(per_cls_boxes)
        #     tem_landms.append(pre_cls_landms)

        # conbine per_cls to a big array
        # scores = np.concatnate(tem_scores, 0)
        # boxes = np.concatnate(tem_boexes, 0)
        # landms = np.concatnate(tem_landms, 0)

        # we need to max scores for each anchor
        labels = np.argmax(scores, axis=-1)
        scores = np.max(scores, axis=-1) # scores : number anchors,

        # do multi cls NMS
        dets = np.hstack((boxes, scores[:, np.newaxis], labels[:, np.newaxis])).astype(np.float32, copy=False) 

        face_idx = np.where(labels==0)
        face_dets = dets[face_idx]
        face_landms = landms[face_idx]

        mask_idx = np.where(labels==1)
        mask_dets = dets[mask_idx]
        mask_landms = landms[mask_idx]

        face_keep = py_cpu_nms(face_dets, args.nms_threshold)
        mask_keep = py_cpu_nms(mask_dets, args.nms_threshold)

        face_dets = face_dets[face_keep,:]
        face_landms = face_landms[face_keep,:]
        mask_dets = mask_dets[mask_keep,:]
        mask_landms = mask_landms[mask_keep,:]

        # dets = dets[keep, :]
        # landms = landms[keep]

        # keep top-K faster NMS
        # dets = dets[:args.keep_top_k, :]
        # landms = landms[:args.keep_top_k, :]

        dets = np.concatenate((face_dets, mask_dets), axis=0)
        landms = np.concatenate((face_landms, mask_landms), axis=0)
        dets = np.concatenate((dets, landms), axis=1)

        # show image
        if args.save_image:
            for b in dets:
                if b[4] < args.vis_thres:
                    continue
                text = "{:.4f}".format(b[4])
                b = list(map(int, b))
                if int(b[5]) == 1:
                    color = (0,255,0)
                else:
                    color = (0, 0, 255)

                cv2.rectangle(img_raw, (b[0], b[1]), (b[2], b[3]), color, 2)
                cx = b[0]
                cy = b[1] + 12
                cv2.putText(img_raw, text, (cx, cy),
                            cv2.FONT_HERSHEY_DUPLEX, 0.5, (255, 255, 255))

                # landms
                cv2.circle(img_raw, (b[6], b[7]), 1, (0, 0, 255), 4)
                cv2.circle(img_raw, (b[8], b[9]), 1, (0, 255, 255), 4)
                cv2.circle(img_raw, (b[10], b[11]), 1, (255, 0, 255), 4)
                cv2.circle(img_raw, (b[12], b[13]), 1, (0, 255, 0), 4)
                cv2.circle(img_raw, (b[14], b[15]), 1, (255, 0, 0), 4)
            # save image

            # name = "test.jpg"
            cv2.imwrite(image_path.replace('.jpg','_1.jpg'), img_raw)
