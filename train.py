import os
import argparse
import yaml
import warnings

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader
from torch.autograd import Variable
from torchvision.utils import save_image

from logger import Logger


from model import UNet
from ops import Colorize
from augmentations import *
from loss import *
from dataloaders import get_dataloader
from optimizers import get_optimizer, get_lr_scheduler

from sklearn.metrics import confusion_matrix


def train(config):
    Dataset = get_dataloader(config['data']['dataset'])
    dataset_path = config['data']['path']

    if 'augmentations' in config['training']:
        augmentation_dict = config['training']['augmentations']
        data_aug = get_augmentations(augmentation_dict)
    else:
        data_aug = None

    data_train = Dataset(root=dataset_path, train=True, augmentations=data_aug)
    data_test = Dataset(root=dataset_path, train=False)

    os.makedirs("samples", exist_ok=True)

    batch_size = config['training']['batch_size']

    training_loader = DataLoader(data_train, batch_size=batch_size, shuffle=True)
    testing_loader = DataLoader(data_test, batch_size=batch_size, shuffle=False)

    # Setup device
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device == 'cpu':
        warnings.warn("You don't have cuda available. The training takes long time.")


    n_classes = config['model']['n_classes']


    model = UNet(num_classes=n_classes,
                 in_channels=config['model']['in_channels'],
                 depth=config['model']['depth'],
                 start_filt_num=config['model']['n_start_filters'],
                 filt_num_factor=config['model']['filter_num_scale'],
                 merge_mode=config['model']['merge_mode']).to(device)


    loss_weight = torch.ones(n_classes)
    loss_weight[0] = 0.6
    loss_weight[2] = 0.8

    loss_param = (config['training']['loss']['name'])#{'weight_ce': loss_weight.to(device)}
    loss_func = get_loss_function(loss_param).to(device)


    Optimizer = get_optimizer(config['training']['optimizer']['name'])
    optimizer_params = {k:v for k, v in config['training']['optimizer'].items()
                        if k!='name'}
    optimizer = Optimizer(model.parameters(), **optimizer_params)

    if 'lr_scheduler' in config['training']:
        Scheduler = get_lr_scheduler(config['training']['lr_scheduler']['name'])
        scheduler_params = {k:v for k, v in config['training']['lr_scheduler'].items()
                        if k!='name'}
        lr_scheduler = Scheduler(optimizer, **scheduler_params)
    else:
        lr_scheduler = None


    epochs = config['training']['n_epochs']
    logger = Logger("./logs")
    iter = 0
    color_transform = Colorize()


    for epoch in range(1, epochs + 1):
        epoch_loss = []
        epoch_dice_loss = []
        epoch_ce_loss = []
        model.train()

        if lr_scheduler is not None:
            lr_scheduler.step()

        for images, labels, weights in training_loader:


            images = Variable(images.to(device))
            labels = Variable(labels.to(device))
            weights = Variable(weights.to(device))

            optimizer.zero_grad()

            outputs = model(images)

            loss, dice_loss, ce_loss = loss_func(outputs, labels, weights)

            loss.backward()
            optimizer.step()

            iter += 1
            # 1. Log scalar values (scalar summary)
            info = {'loss': loss.item(), 'ce_loss': ce_loss.item(), 'dice_loss': dice_loss.item()}

            for tag, value in info.items():
                logger.scalar_summary(tag, value, iter)

            epoch_ce_loss.append(ce_loss.item())
            epoch_dice_loss.append(dice_loss.item())
            epoch_loss.append(loss.item())

        epoch_avg_loss = sum(epoch_loss) / len(epoch_loss)
        epoch_avg_dice = sum(epoch_dice_loss) / len(epoch_dice_loss)
        epoch_avg_ce = sum(epoch_ce_loss) / len(epoch_ce_loss)


        model.eval()

        seg_maps = []
        cnf_matrix_validation = np.zeros((n_classes, n_classes))

        for img, lbls, _  in testing_loader:
            img = Variable(img.to(device))
            lbls = Variable(lbls.to(device))

            with torch.no_grad():
                outs = model(img)
                preds = outs.data.max(1)[1]

            cnf_matrix_validation += confusion_matrix(preds.cpu().view(-1).numpy(), lbls.cpu().view(-1).numpy())


            seg_maps.append((lbls.cpu().squeeze(), preds.cpu().detach()))


        nTotal = len(testing_loader)

        cnf_matrix = cnf_matrix_validation / nTotal

        dice_score = 2 * np.diag(cnf_matrix) / (cnf_matrix.sum(axis=1) + cnf_matrix.sum(axis=0))

        iu = np.diag(cnf_matrix) / (cnf_matrix.sum(axis=1) + cnf_matrix.sum(axis=0) - np.diag(cnf_matrix))

        iu_mean = np.mean(iu)
        dice_score = np.mean(dice_score)

        lbl, pred = random.choice(seg_maps)
        lbl, pred = lbl[0, :, :], pred[0, :, :]
        img_to_save = torch.cat((lbl, pred), dim=1)


        img_to_save = color_transform(img_to_save.unsqueeze(0))
        save_image(img_to_save, "samples/{}_lbl_vs_Pred.png".format(iter))

        out_msg = '[ Epoch {}/{} ] [ Total Loss: {:.4f} ] [Dice Loss: {:.4f}] [CE Loss: {:.4f}] [Avg Dice Score: {:.4f}] [ Mean IoU: {:.4f} ]'.format(
            epoch, epochs,
            epoch_avg_loss,
            epoch_avg_dice,
            epoch_avg_ce,
            dice_score,
            iu_mean
        )
        print(out_msg)




if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config",
                        type=str,
                        help="Path to the configuration file.",
                        default="unet_drive.yml")
    args = parser.parse_args()

    if os.path.isfile(args.config):
        with open(args.config, 'r') as yml_file:
            cfg = yaml.load(yml_file)

    else:
        raise FileNotFoundError("Config file {} doesn't exist!!".format(args.config))



    train(cfg)
