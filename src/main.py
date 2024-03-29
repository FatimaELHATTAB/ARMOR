import copy

import numpy as np
import stats as stats
import torch
import tqdm as tqdm
from torch.utils.tensorboard import SummaryWriter

from ARMOR.src.aggregation import FLaggregate, krum, normBound, rfa, trimmed_mean, average_weights
from ARMOR.src.dataset_utils import get_dataset
from ARMOR.src.models import CNNMnist, CNNFashion_Mnist, CNNCifar
from ARMOR.src.parser import Arguments
from ARMOR.src.train import LocalUpdate
from ARMOR.src.train_dp import LocalUpdateDifferentialPrivacy
from ARMOR.src.utils import weights_init, attack_test_visual_pattern, test_inference

if __name__ == '__main__':

    args = Arguments()
    torch.manual_seed(args.seed)
    device = 'cuda' if args.gpu else 'cpu'
    logger = SummaryWriter('../logs')
    train_dataset, test_dataset, user_groups = get_dataset(args)

    if args.model == 'cnn':
        # Convolutional neural network
        if args.dataset == 'mnist':
            global_model = CNNMnist(args=args)
            # global_model.apply(weight_init)
            global_model.load_state_dict(torch.load('mnist_model.pt'))

        elif args.dataset == 'fmnist':
            global_model = CNNFashion_Mnist(args)
            global_model.load_state_dict(torch.load("fashion_model.pt"))
        elif args.dataset == 'cifar':
            global_model = CNNCifar(args=args)

    global_model.to(device)
    global_model.train()
    global_weights = global_model.state_dict()

    """ 
    Training and testing the attack
    """

    # Training
    train_loss, train_accuracy = [], []
    val_acc_list, net_list = [], []
    cv_loss, cv_acc = [], []
    print_every = 1
    val_loss_pre, counter = 0, 0
    attack_accuracy = []
    test_accuracy = []

    for epoch in tqdm(range(args.epochs)):
        stats_line = ""
        stats_line += str(epoch) + ","
        if epoch == args.savemodel:
            path = "./model_" + str(args.savemodel) + ".pt"
            state_dict = global_model.state_dict()
            torch.save(state_dict, path)
        local_weights, local_losses = [], []
        print(f'\n | Global Training Round : {epoch + 1} |\n')
        path = "./model_last_.pt"
        from shutil import copyfile

        copyfile("./model_last_.pt", "./model_last2_.pt")
        state_dict = global_model.state_dict()
        torch.save(state_dict, path)
        global_model.train()
        if epoch == 0:
            if args.dataset == "mnist":
                old_model_2 = CNNMnist(args)
            else:
                old_model_2 = CNNFashion_Mnist(args)

            old_model_2.apply(weights_init)
            old_model_2 = old_model_2.to(device)

        else:
            old_model_2 = copy.deepcopy(old_model)

        old_model = copy.deepcopy(global_model)
        m = max(int(args.frac * args.num_users), 1)
        # I put num_users - 1 to use an extra subset for training the GAN on the server side
        idxs_users = np.random.choice(range(args.num_users), m, replace=False)

        for idx in idxs_users:
            if args.diffPrivacy:
                local_model = LocalUpdateDifferentialPrivacy(args=args, dataset=train_dataset, idxs=user_groups[idx],
                                                     logger=logger)

            else:
                local_model = LocalUpdate(args=args, dataset=train_dataset, idxs=user_groups[idx], logger=logger)

            print('------------------------------------------')
            print(f'--------------User: {idx}-----------------')
            print('------------------------------------------')

            if args.attack == 1:
                w, loss = local_model.update_weights(
                    model=copy.deepcopy(global_model), global_round=epoch,
                    attack=((idx in args.attackers_list) and (epoch > args.start_round)))
                attackBool = ((idx in args.attackers_list) and (epoch > args.start_round))
                # stats_line += str(attackBool) + ","
            else:
                if args.attack == 2:
                    w, loss = local_model.update_weights(
                        model=copy.deepcopy(global_model), global_round=epoch, attack=(
                                (idx in args.attackers_list) and (epoch % args.attack_step == 0) and (
                                epoch > args.start_round)))
                    attackBool = (idx in args.attackers_list) and (epoch % args.attack_step == 0) and (
                            epoch > args.start_round)
                    print("attack = ", attackBool)
                    # stats_line += str(attackBool) + ","
                else:
                    if args.attack == 3:
                        w, loss = local_model.update_weights(
                            model=copy.deepcopy(global_model), global_round=epoch,
                            attack=((idx in args.attackers_list) and (epoch == args.single_shot_round)))
                        attackBool = (idx in args.attackers_list) and (epoch == args.single_shot_round)
                        print("attack = ", attackBool)
                        # stats_line += str(attackBool) + ","
                    else:
                        if args.attack == 4:
                            w, loss = local_model.update_weights_replacement(copy.deepcopy(global_model), epoch, (
                                    (idx in args.attackers_list) and (epoch % args.attack_step == 0) and (
                                    epoch > args.start_round)))
                        else:
                            w, loss = local_model.update_weights(
                                model=copy.deepcopy(global_model), global_round=epoch, attack=False)

            local_weights.append(copy.deepcopy(w))
            local_losses.append(copy.deepcopy(loss))
        # Update global weights
        # Use Protection mechanism

        if args.detector == 0:
            global_weights = FLaggregate(local_weights, args)
        else:
            if args.detector == 1:
                global_weights = krum(local_weights, args)
            else:
                if args.detector == 2:
                    global_weights = normBound(local_weights, args)
                else:
                    if args.detector == 5:
                        global_weights = rfa(local_weights)
                    else:
                        if args.detector == 6:
                            global_weights = trimmed_mean(local_weights)

        global_model.load_state_dict(global_weights)
        path = "./model_current_.pt"
        state_dict = global_model.state_dict()
        torch.save(state_dict, path)

        if args.detector == 3:
            poisoning_flag, runtime, global_model, diff_loss, loss_inspected, loss_clean, diff_accuracy, accuracy_clean, accuracy_inspected, output_average_clean, output_average_inspected = GAN_ARMOR(
                args)
        else:
            global_weights = average_weights(local_weights)

        loss_avg = sum(local_losses) / len(local_losses)
        train_loss.append(loss_avg)

        # Calculate avg training accuracy over all users at every epoch
        list_acc, list_loss = [], []
        global_model.eval()
        for c in range(args.num_users):
            local_model = LocalUpdate(args=args, dataset=train_dataset,
                                      idxs=user_groups[idx], logger=logger)
            acc, loss = local_model.inference(model=global_model)
            list_acc.append(acc)
            list_loss.append(loss)
        train_accuracy.append(sum(list_acc) / len(list_acc))

        attack_accuracy.append(attack_test_visual_pattern(test_dataset, global_model))
        stats_line += str(attack_accuracy[-1]) + ","
        print('------------attack accuracy --------------')
        print("|---- Test Attack Accuracy: {:.2f}%".format(attack_accuracy[-1]))
        if attack_accuracy[-1] > 90:
            print("**********************************save attack model ***********************************")
            path = "./model_attack_.pt"
            state_dict = global_model.state_dict()
            torch.save(state_dict, path)

        test_acc, test_loss = test_inference(args, global_model, test_dataset)
        test_accuracy.append(test_acc)
        stats_line += str(test_accuracy[-1]) + "\n"
        stats.write(stats_line)
        # print global training loss after every 'i' rounds
        if (epoch + 1) % print_every == 0:
            print(f' \nAvg Training Stats after {epoch + 1} global rounds:')
            print(f'Training Loss : {np.mean(np.array(train_loss))}')
            print('Train Accuracy: {:.2f}% \n'.format(100 * train_accuracy[-1]))

    # test_acc, test_loss = test_inference(args, global_model, test_dataset)
    stats.close()
    print(f' \n Results after {args.epochs} global rounds of training:')
    print("|---- Avg Train Accuracy: {:.2f}%".format(100 * train_accuracy[-1]))
    print("|---- Test Accuracy: {:.2f}%".format(100 * test_accuracy[-1]))
