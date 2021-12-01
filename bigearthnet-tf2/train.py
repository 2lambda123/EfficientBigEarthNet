#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# TODO
#
import numpy as np
from gradcam import GradCAM
import tensorflow as tf
import random as rn
import argparse
import os
import json
import time
from datetime import datetime
from inputs import create_batched_dataset
from metrics import CustomMetrics
from models import MODELS_CLASS
import models
SEED = 42


def evaluate_model(model, batched_dataset, nb_class, args, gradcam=False):
    """Evaluate model using the eval dataset
    """
    print('Running model on validation dataset')
    custom_metrics = CustomMetrics(nb_class=nb_class)

    processed_imgs = 0
    start = time.time()
    for i, single_batch in enumerate(iter(batched_dataset)):
        x_all = [
            single_batch["B02"],
            single_batch["B03"],
            single_batch["B04"],
            single_batch["B05"],
            single_batch["B06"],
            single_batch["B07"],
            single_batch["B08"],
            single_batch["B8A"],
            single_batch["B11"],
            single_batch["B12"],
        ]
        y = single_batch[args["label_type"] + "_labels_multi_hot"]
        # Compare predicted label to actual label
        y_ = model(x_all, training=False)

        # Update all custom metrics
        custom_metrics.update_state(y, y_)
        
        processed_imgs += x_all[0].shape[0]

        if gradcam:
            gradcam_x_all = []
            for j in range(len(single_batch)):
                gradcam_x_all.append([
                    single_batch["B02"][j:j+1][:][:],
                    single_batch["B03"][j:j+1][:][:],
                    single_batch["B04"][j:j+1][:][:],
                    single_batch["B05"][j:j+1][:][:],
                    single_batch["B06"][j:j+1][:][:],
                    single_batch["B07"][j:j+1][:][:],
                    single_batch["B08"][j:j+1][:][:],
                    single_batch["B8A"][j:j+1][:][:],
                    single_batch["B11"][j:j+1][:][:],
                    single_batch["B12"][j:j+1][:][:],
                ])

            patch_names = single_batch["patch_name"].values.numpy().tolist()

            for index, x in enumerate(gradcam_x_all):
                GradCAM(model, x, y[index], patch_names[index])
    
    if args['worker_index'] == 0:
        print('Inference rate: {} images/sec'.format(processed_imgs // (time.time() - start)))

    micro_precision, macro_precision, micro_recall, macro_recall, micro_accuracy, macro_accuracy, micro_fscore, macro_fscore = custom_metrics.result()
    if args['parallel']:
        print('Time : ' + str(datetime.now().strftime('%Y-%m-%d %H:%M:%S')) + ' Process %d Reduce ' % hvd.rank(), flush=True)
        hvd.join()
        micro_precision = hvd.allreduce(micro_precision)
        macro_precision = hvd.allreduce(macro_precision)
        micro_recall = hvd.allreduce(micro_recall)
        macro_recall = hvd.allreduce(macro_recall)
        micro_accuracy = hvd.allreduce(micro_accuracy)
        macro_accuracy = hvd.allreduce(macro_accuracy)
        micro_fscore = hvd.allreduce(micro_fscore)
        macro_fscore = hvd.allreduce(macro_fscore)

    return (
        micro_precision,
        macro_precision,
        micro_recall,
        macro_recall,
        micro_accuracy,
        macro_accuracy,
        micro_fscore,
        macro_fscore
    )


def _write_summary(summary_writer, custom_metrics, epoch):
    (
        epoch_micro_precision,
        epoch_macro_precision,
        epoch_micro_recall,
        epoch_macro_recall,
        epoch_micro_accuracy,
        epoch_macro_accuracy,
        epoch_micro_fscore,
        epoch_macro_fscore
    ) = custom_metrics

    with summary_writer.as_default():
        tf.summary.scalar('micro_precision', epoch_micro_precision, step=epoch)
        tf.summary.scalar('macro_precision', epoch_macro_precision, step=epoch)
        tf.summary.scalar('micro_recall', epoch_micro_recall, step=epoch)
        tf.summary.scalar('macro_recall', epoch_macro_recall, step=epoch)
        tf.summary.scalar('micro_accuracy', epoch_micro_accuracy, step=epoch)
        tf.summary.scalar('macro_accuracy', epoch_macro_accuracy, step=epoch)
        tf.summary.scalar('micro_fscore', epoch_micro_fscore, step=epoch)
        tf.summary.scalar('macro_fscore', epoch_macro_fscore, step=epoch)


def run_model(args):
    print("TensorFlow version: {}".format(tf.__version__))
    print("Eager execution: {}".format(tf.executing_eagerly()))
    print("Running using random seed: {}".format(SEED))

    print("Batch size: {}".format(args["batch_size"]))
    print("Epochs: {}".format(args["nb_epoch"]))

    rn.seed(SEED)
    np.random.seed(SEED)
    tf.random.set_seed(SEED)

    # Create our data pipeline
    train_batched_dataset = create_batched_dataset(
        args["tr_tf_record_files"],
        args["batch_size"],
        args["shuffle_buffer_size"],
        args["label_type"],
        args['num_workers'],
        args['worker_index'],
        augment=args['augment']
    )

    val_batched_dataset = create_batched_dataset(
        args["val_tf_record_files"],
        args["batch_size"],
        args["shuffle_buffer_size"],
        args["label_type"],
        args['num_workers'],
        args['worker_index'],
    )

    test_batched_dataset = create_batched_dataset(
        args['test_tf_record_files'],
        args['batch_size'],
        args['shuffle_buffer_size'],
        args['label_type'],
        args['num_workers'],
        args['worker_index'],
    )

    # Create our model
    nb_class = 19 if args["label_type"] == "BigEarthNet-19" else 43

    try:
        bigearth_model_class = MODELS_CLASS[args["model_name"]]
    except:
        bigearth_model_class = MODELS_CLASS["dense"]

    print('Creating model: {}'.format(args['model_name']))
    if args['model_name'] in ('EfficientNet', 'WideResNet'):
        bigearth_model = getattr(models, bigearth_model_class)(nb_class=nb_class, coefficients=args['hparams'])
    else:
        bigearth_model = getattr(models, bigearth_model_class)(nb_class=nb_class)
    model = bigearth_model.model
    if args['worker_index'] == 0:
        print(model.summary())

    # DEBUG (use this to understand what the iterators are returning)
    debug = False
    if debug:
        single_batch = next(iter(train_batched_dataset))
        x_all = [
            single_batch["B02"],
            single_batch["B03"],
            single_batch["B04"],
            single_batch["B05"],
            single_batch["B06"],
            single_batch["B07"],
            single_batch["B08"],
            single_batch["B8A"],
            single_batch["B11"],
            single_batch["B12"],
        ]
        y = single_batch[args["label_type"] + "_labels_multi_hot"]
        y_ = model(x_all, training=False)

    # Create loss
    loss = tf.keras.losses.BinaryCrossentropy(label_smoothing=tf.cast(args["label_smoothing"], tf.float64))
    print('Learning Rate : ',args['learning_rate'])
    # Setup training step
    @tf.function
    def training_step(inputs, targets, first_batch):
        with tf.GradientTape() as tape:
            y_pred = model(inputs, training=True)
            loss_value = loss(y_true=targets, y_pred=y_pred)

        if args['parallel']:
            # Horovod: add Horovod Distributed GradientTape.
            tape = hvd.DistributedGradientTape(tape)

        grads = tape.gradient(loss_value, model.trainable_variables)
        optimizer.apply_gradients(zip(grads, model.trainable_variables))

        if args['parallel']:
            if first_batch:
                hvd.broadcast_variables(model.variables, root_rank=0)
                hvd.broadcast_variables(optimizer.variables(), root_rank=0)

        return loss_value, y_pred

    # Setup optimizer
    step_epochs = args['decay_step']
    nb_iterations_per_epoch = (args["training_size"] / args['num_workers']) / args["batch_size"]

    decay_step = int(step_epochs * nb_iterations_per_epoch)#*args['num_workers']
    back_passes = args['backward_passes']
    decay_rate = args['decay_rate']
    print('decay step : ', decay_step)
    print('Back passes : ', back_passes)
    print('Decay rate : ', decay_rate)
    learning_rate = args['learning_rate']*args['num_workers']


    optimizer = tf.keras.optimizers.Adam(learning_rate = learning_rate)
    if args['num_workers'] > 2:
        optimizer = hvd.DistributedOptimizer(optimizer, backward_passes_per_step = back_passes)
    
    # Setup metrics logging
    logdir = "logs/scalars/" + datetime.now().strftime("%Y%m%d-%H%M%S")
    train_summary_writer = tf.summary.create_file_writer(os.path.join(logdir, 'train'))
    train_summary_writer.set_as_default()
    test_summary_writer = tf.summary.create_file_writer(os.path.join(logdir, 'test'))
    checkpoint_dir = './checkpoint_' + args['model_name'] + '/checkpoints'
    checkpoint = tf.train.Checkpoint(model=model, optimizer=optimizer)
    # The main loop
    batch_size = args["batch_size"]
    epoch_custom_metrics = CustomMetrics(nb_class=nb_class)
    bestfscore = 0

    if args['mode'] == 'eval':
        check_dir = args['eval_checkpoint']
        checkpoint_test = tf.train.Checkpoint(model=model, optimizer=optimizer)
        checkpoint_test.restore(tf.train.latest_checkpoint(check_dir))
        test_eval = evaluate_model(model, test_batched_dataset, nb_class, args, True)
        (
            epoch_micro_precision,
            epoch_macro_precision,
            epoch_micro_recall,
            epoch_macro_recall,
            epoch_micro_accuracy,
            epoch_macro_accuracy,
            epoch_micro_fscore,
            epoch_macro_fscore
        ) = test_eval
        if args['worker_index'] == 0:
            print('\n\n\n\n Test Scores \n\n\n=============')

            print(
                "Evaluation : micro: accuracy: {:.3f}, precision: {:.3f}, recall: {:.3f}, f-score: {:.3f}".format(
                     epoch_micro_accuracy, epoch_micro_precision, epoch_micro_recall, epoch_micro_fscore
                )
            )
            print(
                " macro: accuracy: {:.3f}, precision: {:.3f}, recall: {:.3f}, f-score: {:.3f}".format(
                     epoch_macro_accuracy, epoch_macro_precision, epoch_macro_recall, epoch_macro_fscore
                )
            )
        return 0

    if args['worker_index'] == 0:
        start = time.time()
    for epoch in range(args["nb_epoch"]):
        if epoch % args['decay_step']==0 and epoch>0:
            optimizer.lr = optimizer.lr * args['decay_rate']
            #print('Updated Learning rate : ', optimizer.lr)
        epoch_time = time.time()
        print("\nProcess {} : Starting epoch {} ".format(args['worker_index'], epoch))
        print('Learning rate: {0:.6f}'.format(optimizer._decayed_lr('float32').numpy()))

        epoch_loss_avg = tf.keras.metrics.Mean(dtype='float64')
        epoch_custom_metrics.reset_states()

        nb_iterations = (args["training_size"] / args['num_workers']) / args["batch_size"]
        if (args["training_size"] / args['num_workers']) % args["batch_size"] != 0:
            nb_iterations += 1

        # if args["worker_index"]==0:
        progress_bar = tf.keras.utils.Progbar(target=nb_iterations)

        batch_iterator = iter(train_batched_dataset)
        for i, single_batch in enumerate(batch_iterator):
            x_all = [
                single_batch["B02"],
                single_batch["B03"],
                single_batch["B04"],
                single_batch["B05"],
                single_batch["B06"],
                single_batch["B07"],
                single_batch["B08"],
                single_batch["B8A"],
                single_batch["B11"],
                single_batch["B12"],
            ]
            y = single_batch[args["label_type"] + "_labels_multi_hot"]

            # Optimize the model
            first_batch = (i == 0 and epoch == 0)
            loss_value, y_ = training_step(x_all, y, first_batch)
            # Track progress
            epoch_loss_avg.update_state(loss_value)  # Add current batch loss
            # Compare predicted label to actual label
            #y_ = model(x_all, training=False)
            # Update all custom metrics
            epoch_custom_metrics.update_state(y, y_)
            if i % 20 == 0 and args['worker_index'] == 0:
                # print('Process %d Epoch %d Iteration %d'%(args['worker_index'],epoch,i),flush=True)
                print("Process {:01d}:  Epoch {:03d}: Iteration {:03d} Loss: {:.3f}".format(args['worker_index'], epoch,
                                                                                            i, loss_value.numpy()))
            progress_bar.update(i + 1)

        # End epoch

        if epoch % 10 == 0 or epoch == args['nb_epoch'] - 1:
            if args['worker_index'] == 0:
                eval_start = time.time()

            tf.summary.scalar('loss', epoch_loss_avg.result(), step=epoch)
            _write_summary(train_summary_writer, epoch_custom_metrics.result(), epoch)
            print("Process {:01d}:  Epoch : {:03d}: Loss: {:.3f}".format(args['worker_index'], epoch,
                                                                         epoch_loss_avg.result()))
            # Evaluate model using the eval dataset
            evaluation = evaluate_model(model, val_batched_dataset, nb_class, args)

            _write_summary(test_summary_writer, evaluation, epoch)

            (
                epoch_micro_precision,
                epoch_macro_precision,
                epoch_micro_recall,
                epoch_macro_recall,
                epoch_micro_accuracy,
                epoch_macro_accuracy,
                epoch_micro_fscore,
                epoch_macro_fscore
            ) = evaluation
            if args['worker_index'] == 0 and epoch_micro_fscore > bestfscore:
                bestfscore = epoch_micro_fscore.numpy()
                print("Process {:01d}: New Best F-Score : {:.3f}\n Epoch : {:03d} Writing Checkpoint".format(args['worker_index'], bestfscore, epoch))
                print("Process {:01d}:  Epoch : {:03d} Writing Checkpoint".format(args['worker_index'], epoch))
                checkpoint.save(checkpoint_dir)
                print(
                    "Epoch {:03d}: micro: accuracy: {:.3f}, precision: {:.3f}, recall: {:.3f}, f-score: {:.3f}".format(
                        epoch, epoch_micro_accuracy, epoch_micro_precision, epoch_micro_recall, epoch_micro_fscore
                    )
                )
                print(
                    "Epoch {:03d}: macro: accuracy: {:.3f}, precision: {:.3f}, recall: {:.3f}, f-score: {:.3f}".format(
                        epoch, epoch_macro_accuracy, epoch_macro_precision, epoch_macro_recall, epoch_macro_fscore
                    )
                )
            if args['worker_index'] == 0:
                eval_end = time.time()
                eval_time = eval_end - eval_start
                eval_hours, eval_remainder = divmod(eval_end-eval_start, 3600)
                eval_minutes, eval_seconds = divmod(eval_remainder, 60)

                start = start + eval_time
                train_end = time.time()
                train_time = train_end - start
                train_hours, train_remainder = divmod(train_time, 3600)
                train_minutes, train_seconds = divmod(train_remainder, 60)

                print('Train took: {:0>2}hr. {:0>2}min. {:05.2f}sec.'.format(int(train_hours),int(train_minutes),train_seconds))
                print('Validation took: {:0>2}hr. {:0>2}min. {:05.2f}sec.'.format(int(eval_hours),int(eval_minutes),eval_seconds))


            if epoch == args['nb_epoch'] - 1:
                checkpoint_test = tf.train.Checkpoint(model=model, optimizer=optimizer)
                checkpoint_test.restore(tf.train.latest_checkpoint(checkpoint_dir))
                test_eval = evaluate_model(model, test_batched_dataset, nb_class, args)
                (
                    epoch_micro_precision,
                    epoch_macro_precision,
                    epoch_micro_recall,
                    epoch_macro_recall,
                    epoch_micro_accuracy,
                    epoch_macro_accuracy,
                    epoch_micro_fscore,
                    epoch_macro_fscore
                ) = test_eval
                if args['worker_index'] == 0:
                    print('\n\n\n\n Test Scores \n\n\n=============')

                    print(
                        "Epoch {:03d}: micro: accuracy: {:.3f}, precision: {:.3f}, recall: {:.3f}, f-score: {:.3f}".format(
                            epoch, epoch_micro_accuracy, epoch_micro_precision, epoch_micro_recall, epoch_micro_fscore
                        )
                    )
                    print(
                        "Epoch {:03d}: macro: accuracy: {:.3f}, precision: {:.3f}, recall: {:.3f}, f-score: {:.3f}".format(
                            epoch, epoch_macro_accuracy, epoch_macro_precision, epoch_macro_recall, epoch_macro_fscore
                        )
                    )

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Training script")
    parser.add_argument("--configs", required=False, default='configs/base.json', help="JSON config file")
    parser.add_argument("--parallel", required=False, default=False, help="Enable parallelism")
    parser_args = parser.parse_args()

    with open(parser_args.configs, "rb") as f:
        args = json.load(f)
        args.update(vars(parser_args))

    gpus = tf.config.experimental.list_physical_devices('GPU')
    for d in gpus:
        tf.config.experimental.set_memory_growth(d, True)

    if parser_args.parallel:
        import horovod.tensorflow as hvd

        hvd.init()
        if gpus:
            tf.config.experimental.set_visible_devices(gpus[hvd.local_rank()], 'GPU')
        args['num_workers'] = hvd.size()
        args['worker_index'] = hvd.rank()
    else:
        args['num_workers'] = 1
        args['worker_index'] = 0

    run_model(args)

    if parser_args.parallel:
        hvd.join()