"""Tests for AdaNet AutoEnsembleEstimator.

Copyright 2018 The AdaNet Authors. All Rights Reserved.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import shutil

from absl.testing import parameterized
from adanet import tf_compat
from adanet.autoensemble.estimator import AutoEnsembleEstimator
import tensorflow as tf

from tensorflow.python.estimator.export import export

tf.logging.set_verbosity(tf.logging.INFO)


class AutoEnsembleEstimatorTest(parameterized.TestCase, tf.test.TestCase):

  def setUp(self):
    # Setup and cleanup test directory.
    self.test_subdirectory = os.path.join(tf.flags.FLAGS.test_tmpdir, self.id())
    shutil.rmtree(self.test_subdirectory, ignore_errors=True)
    os.makedirs(self.test_subdirectory)

  def tearDown(self):
    shutil.rmtree(self.test_subdirectory, ignore_errors=True)

  @parameterized.named_parameters(
      {
          "testcase_name": "dict_candidate_pool",
          "list_candidate_pool": False,
      }, {
          "testcase_name": "list_candidate_pool",
          "list_candidate_pool": True,
      })
  def test_auto_ensemble_estimator_lifecycle(self, list_candidate_pool):
    features = {"input_1": [[1., 0.]]}
    labels = [[1.]]

    run_config = tf.estimator.RunConfig(tf_random_seed=42)
    head = tf.contrib.estimator.regression_head(
        loss_reduction=tf.losses.Reduction.SUM_OVER_BATCH_SIZE)

    optimizer = tf.train.GradientDescentOptimizer(learning_rate=.01)
    feature_columns = [tf.feature_column.numeric_column("input_1", shape=[2])]

    def train_input_fn():
      input_features = {}
      for key, feature in features.items():
        input_features[key] = tf.constant(feature, name=key)
      input_labels = tf.constant(labels, name="labels")
      return input_features, input_labels

    def test_input_fn():
      input_features = tf.data.Dataset.from_tensors([
          tf.constant(features["input_1"])
      ]).make_one_shot_iterator().get_next()
      return {"input_1": input_features}, None

    if hasattr(tf.estimator, "LinearEstimator"):
      linear_estimator_fn = tf.estimator.LinearEstimator
    else:
      linear_estimator_fn = tf.contrib.estimator.LinearEstimator
    if hasattr(tf.estimator, "DNNEstimator"):
      dnn_estimator_fn = tf.estimator.DNNEstimator
    else:
      dnn_estimator_fn = tf.contrib.estimator.DNNEstimator

    candidate_pool = {
        "linear":
            linear_estimator_fn(
                head=head, feature_columns=feature_columns,
                optimizer=optimizer),
        "dnn":
            dnn_estimator_fn(
                head=head,
                feature_columns=feature_columns,
                optimizer=optimizer,
                hidden_units=[3])
    }
    if list_candidate_pool:
      candidate_pool = [candidate_pool[k] for k in sorted(candidate_pool)]

    estimator = AutoEnsembleEstimator(
        head=head,
        candidate_pool=candidate_pool,
        max_iteration_steps=10,
        force_grow=True,
        model_dir=self.test_subdirectory,
        config=run_config)

    # Train for three iterations.
    estimator.train(input_fn=train_input_fn, max_steps=30)

    # Evaluate.
    eval_results = estimator.evaluate(input_fn=train_input_fn, steps=3)

    want_loss = .209
    if tf_compat.version_greater_or_equal("1.10.0") and (
        not tf_compat.version_greater_or_equal("1.12.0")):
      # Only TF 1.10 and 1.11.
      want_loss = .079514
    self.assertAllClose(want_loss, eval_results["loss"], atol=.05)

    # Predict.
    predictions = estimator.predict(input_fn=test_input_fn)
    for prediction in predictions:
      self.assertIsNotNone(prediction["predictions"])

    # Export SavedModel.
    def serving_input_fn():
      """Input fn for serving export, starting from serialized example."""
      serialized_example = tf.placeholder(
          dtype=tf.string, shape=(None), name="serialized_example")
      for key, value in features.items():
        features[key] = tf.constant(value)
      return export.SupervisedInputReceiver(
          features=features,
          labels=tf.constant(labels),
          receiver_tensors=serialized_example)

    export_dir_base = os.path.join(self.test_subdirectory, "export")
    export_saved_model_fn = getattr(estimator, "export_saved_model", None)
    if not callable(export_saved_model_fn):
      export_saved_model_fn = estimator.export_savedmodel
    export_saved_model_fn(
        export_dir_base=export_dir_base,
        serving_input_receiver_fn=serving_input_fn)

  def test_extra_checkpoint_saver_hook(self):
    """Tests b/122795064."""

    features = {"input_1": [[1., 0.]]}
    labels = [[1.]]

    run_config = tf.estimator.RunConfig(tf_random_seed=42)
    head = tf.contrib.estimator.binary_classification_head(
        loss_reduction=tf.losses.Reduction.SUM_OVER_BATCH_SIZE)

    optimizer = tf.train.GradientDescentOptimizer(learning_rate=.01)
    feature_columns = [tf.feature_column.numeric_column("input_1", shape=[2])]

    estimator = AutoEnsembleEstimator(
        head=head,
        candidate_pool=[
            tf.estimator.LinearClassifier(
                n_classes=2,
                feature_columns=feature_columns,
                optimizer=optimizer),
            tf.estimator.DNNClassifier(
                n_classes=2,
                feature_columns=feature_columns,
                optimizer=optimizer,
                hidden_units=[3]),
        ],
        max_iteration_steps=3,
        force_grow=True,
        model_dir=self.test_subdirectory,
        config=run_config)

    ckpt_dir = os.path.join(self.test_subdirectory)
    hooks = [tf.train.CheckpointSaverHook(ckpt_dir, save_steps=1)]

    def train_input_fn():
      input_features = {}
      for key, feature in features.items():
        input_features[key] = tf.constant(feature, name=key)
      input_labels = tf.constant(labels, name="labels")
      return input_features, input_labels

    estimator.train(input_fn=train_input_fn, max_steps=6, hooks=hooks)


if __name__ == "__main__":
  tf.test.main()
