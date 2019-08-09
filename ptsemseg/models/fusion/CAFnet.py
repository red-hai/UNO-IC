import torch.nn as nn
from torch.autograd import Variable

from .fusion import *
from ptsemseg.models.recalibrator import *
from ptsemseg.models.segnet_mcdo import *

class CAFnet(nn.Module):
    def __init__(self,
                 backbone="segnet",
                 n_classes=21,
                 in_channels=3,
                 is_unpooling=True,
                 input_size=(473, 473),
                 batch_size=2,
                 version=None,
                 mcdo_passes=1,
                 dropoutP=0.1,
                 full_mcdo=False,
                 start_layer="down1",
                 end_layer="up1",
                 reduction=1.0,
                 device="cpu",
                 recalibrator="None",
                 temperatureScaling=False,
                 bins=0,
                 fusion_module="1.1",
                 resume_rgb="./models/Segnet/rgb_Segnet/rgb_segnet_mcdo_airsim_T000+T050.pkl",
                 resume_d="./models/Segnet/d_Segnet/d_segnet_mcdo_airsim_T000+T050.pkl"
                 ):
        super(CAFnet, self).__init__()

        self.rgb_segnet = segnet_mcdo(n_classes, in_channels, is_unpooling, input_size, batch_size, version,
                                      mcdo_passes, dropoutP, full_mcdo, start_layer,
                                      end_layer, reduction, device, recalibrator, temperatureScaling, bins)

        self.d_segnet = segnet_mcdo(n_classes, in_channels, is_unpooling, input_size, batch_size, version,
                                    mcdo_passes, dropoutP, full_mcdo, start_layer,
                                    end_layer, reduction, device, recalibrator, temperatureScaling, bins)

        # self.rgb_scaling = torch.nn.Parameter(torch.ones(1))
        # self.d_scaling = torch.nn.Parameter(torch.ones(1))

        self.rgb_segnet = torch.nn.DataParallel(self.rgb_segnet, device_ids=range(torch.cuda.device_count()))
        self.d_segnet = torch.nn.DataParallel(self.d_segnet, device_ids=range(torch.cuda.device_count()))


        # initialize segnet weights
        if resume_rgb:
            self.loadModel(self.rgb_segnet, resume_rgb)
        if resume_d:
            self.loadModel(self.d_segnet, resume_d)

        # freeze segnet networks
        for param in self.rgb_segnet.parameters():
            param.requires_grad = False
        for param in self.d_segnet.parameters():
            param.requires_grad = False
        
        self.fusion = self._get_fusion_module(fusion_module)

    def forward(self, inputs):

        # Freeze batchnorm
        self.rgb_segnet.eval()
        self.d_segnet.eval()

        inputs_rgb = inputs[:, :3, :, :]
        inputs_d = inputs[:, 3:, :, :]

        mean_rgb, var_rgb, entropy_rgb, MI_rgb = self.rgb_segnet.module.forwardMCDO(inputs_rgb)
        mean_d, var_d, entropy_rgb, MI_rgb = self.d_segnet.module.forwardMCDO(inputs_d)
        
        s = var_rgb.shape
        
        var_rgb = torch.mean(var_rgb, 1).view(-1, 1, s[2], s[3])
        var_d = torch.mean(var_d, 1).view(-1, 1, s[2], s[3]) 
        
        # mean_d = mean_d / self.d_scaling
        # mean_rgb = mean_rgb / self.rgb_scaling

        x = self.fusion(mean_rgb, mean_d, var_rgb, var_d)

        return x

    def loadModel(self, model, path):
        model_pkl = path

        print(path)
        if os.path.isfile(model_pkl):
            pretrained_dict = torch.load(model_pkl)['model_state']
            model_dict = model.state_dict()

            # 1. filter out unnecessary keys
            pretrained_dict = {k: v.resize_(model_dict[k].shape) for k, v in pretrained_dict.items() if (
                    k in model_dict)}  # and ((model!="fuse") or (model=="fuse" and not start_layer in k))}

            # 2. overwrite entries in the existing state dict
            model_dict.update(pretrained_dict)

            # 3. load the new state dict
            model.load_state_dict(pretrained_dict)
        else:
            print("model not found")
            exit()
            
    def _get_fusion_module(self, name):

        name = str(name)

        return {
            "GatedFusion": GatedFusion,
            "1.0": GatedFusion,
            "ConditionalAttentionFusion": ConditionalAttentionFusion,
            "1.1": ConditionalAttentionFusion,

            "PreweightedGatedFusion": PreweightedGatedFusion,
            "1.2": PreweightedGatedFusion,
            "UncertaintyGatedFusion": UncertaintyGatedFusion,
            "1.3": UncertaintyGatedFusion,
            "ConditionalAttentionFusionv2": ConditionalAttentionFusionv2,
            "2.1": ConditionalAttentionFusionv2,
            "ScaledMultiply": ScaledMultiply,
            "ScaledAverage": ScaledAverage,
        }[name]