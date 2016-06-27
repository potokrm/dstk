from __future__ import division
from sklearn.tree import DecisionTreeRegressor
from sklearn.tree._tree import TREE_LEAF, TREE_UNDEFINED, Tree
from sklearn.metrics import accuracy_score, precision_score, recall_score, roc_auc_score
import numpy as np
import bisect
from collections import Counter


class ShapeFunction(object):

    def __init__(self, list_of_splits, list_of_values):
        assert len(list_of_splits) == len(list_of_values), 'splits and values need to be of the same length'
        assert all(list_of_splits[i] <= list_of_splits[i+1] for i in xrange(len(list_of_splits)-1)), 'range of splits has to be sorted!'

        self.splits = np.asarray(list_of_splits)
        self.values = np.asarray(list_of_values)

    def get_value(self, feature_value):
        idx = bisect.bisect(self.splits, feature_value)
        if idx == len(self.splits):
            idx = -1
        return self.values[idx]

    def multiply(self, const):
        return ShapeFunction(self.splits, const * self.values)

    def add(self, other):
        return self.__add__(other)

    def __add__(self, other):

        assert isinstance(other, ShapeFunction), "Can only add other shape function"

        new_splits = self.splits
        new_vals = self.values

        for split, val in zip(other.splits, other.values):
            idx = bisect.bisect(new_splits, split)
            new_val = val
            if split in new_splits:
                idx_2 = np.argwhere(new_splits == split)
                new_vals[idx_2] = new_vals[idx_2] + new_val
            elif idx == len(new_splits) and (~np.isposinf(split)):
                new_splits = np.append(new_splits, split)
                new_vals = np.append(new_vals, new_val)
            elif np.isposinf(split):
                new_vals[-1] = new_vals[-1] + new_val
            else:
                new_splits = np.insert(new_splits, idx, split)
                new_vals = np.insert(new_vals, idx, new_val)

        return ShapeFunction(new_splits, new_vals)

    def __str__(self):
        return ''.join(['< {} : {}\n'.format(tup[0], tup[1]) for tup in zip(self.splits, self.values)])

    def equals(self, other):
        return (self.splits == other.splits).all() and (self.values == other.values).all()


class GAM(object):

    def __init__(self, **kwargs):
        self.shapes = dict()
        self.is_fit = False
        self._n_dim = None
        self.initialized = False
        self._recording = {
            'epoch': 0,
            'costs': {
                'accuracy': [],
                'precision': [],
                'recall': [],
                'roc_auc': []
            }
        }

        criterion = kwargs.get('criterion', 'mse')
        splitter = kwargs.get('splitter', 'best')
        max_depth = kwargs.get('max_depth', None)
        min_samples_split = kwargs.get('min_samples_split', 2)
        min_samples_leaf = kwargs.get('min_samples_leaf', 1)
        min_weight_fraction_leaf = kwargs.get('min_weight_fraction_leaf', 0.0)
        max_features = kwargs.get('max_features', None)
        random_state = kwargs.get('random_state', None)
        max_leaf_nodes = kwargs.get('max_leaf_nodes', None)
        presort = kwargs.get('presort', False)

        self.dtr = DecisionTreeRegressor(criterion=criterion,
                                         splitter=splitter,
                                         max_depth=max_depth,
                                         min_samples_split=min_samples_split,
                                         min_samples_leaf=min_samples_leaf,
                                         min_weight_fraction_leaf=min_weight_fraction_leaf,
                                         max_features=max_features,
                                         random_state=random_state,
                                         max_leaf_nodes=max_leaf_nodes,
                                         presort=presort)

    @staticmethod
    def _recurse(tree, feature_vec):

        assert isinstance(tree, Tree), "Tree is not a sklearn Tree"

        break_idx = 0
        node_id = 0

        if not isinstance(feature_vec, list):
            feature_vec = list([feature_vec])

        leaf_node_id = 0
        lower = np.NINF
        upper = np.PINF

        while (node_id != TREE_LEAF) & (tree.feature[node_id] != TREE_UNDEFINED):
            feature_idx = tree.feature[node_id]
            threshold = tree.threshold[node_id]

            if feature_vec[feature_idx] <= threshold:
                upper = threshold
                if (tree.children_left[node_id] != TREE_LEAF) and (tree.children_left[node_id] != TREE_UNDEFINED):
                    leaf_node_id = tree.children_left[node_id]
                node_id = tree.children_left[node_id]
            else:
                lower = threshold
                if (tree.children_right[node_id] == TREE_LEAF) and (tree.children_right[node_id] != TREE_UNDEFINED):
                    leaf_node_id = tree.children_right[node_id]
                node_id = tree.children_right[node_id]

            break_idx += 1
            if break_idx > 2 * tree.node_count:
                raise RuntimeError("infinite recursion!")

        return leaf_node_id, lower, upper

    @staticmethod
    def _get_sum_of_gamma_correction(tree, data, labels):

        num_of_samples = {}
        sum_of_labels = {}
        weighted_sum_of_labels = {}
        set_of_boundaries = set()

        for vec, label in zip(data, labels):
            node_id, lower, upper = GAM._recurse(tree, vec)

            if node_id in sum_of_labels.keys():
                num_of_samples[node_id] += 1
                sum_of_labels[node_id] += label
                weighted_sum_of_labels[node_id] += np.abs(label) * (2-np.abs(label))
            else:
                num_of_samples[node_id] = 1
                sum_of_labels[node_id] = label
                weighted_sum_of_labels[node_id] = np.abs(label) * (2-np.abs(label))

            set_of_boundaries.add((node_id, lower, upper))

        lst_of_sorted_boundaries = sorted(set_of_boundaries, key=lambda x: x[1])
        split_values = [tup[2] for tup in lst_of_sorted_boundaries]
        node_keys = [tup[0] for tup in lst_of_sorted_boundaries]
        values = [(sum_of_labels[key]) / float(weighted_sum_of_labels[key]) for key in node_keys]
        return ShapeFunction(split_values, values)

    def _get_shape_for_attribute(self, attribute_data, labels):
        self.dtr.fit(attribute_data.reshape(-1, 1), labels)
        return GAM._get_sum_of_gamma_correction(self.dtr.tree_, attribute_data, labels)

    def logit_score(self, vec):
        return np.sum([func.get_value(vec[key]) for key, func in self.shapes.iteritems()])

    def score(self, vec):
        return 1. / (1 + np.exp( 1 * np.sum([func.get_value(vec[key]) for key, func in self.shapes.iteritems()]))),\
               1. / (1 + np.exp(-1 * np.sum([func.get_value(vec[key]) for key, func in self.shapes.iteritems()])))

    def _train_cost(self, data, labels):
        pred_scores = np.asarray([self.score(vec) for vec in data], dtype='float')
        pred_labels = [2 * np.argmax(score) - 1 for score in pred_scores]
        self._recording['costs']['accuracy'].append(accuracy_score(labels, pred_labels))
        self._recording['costs']['precision'].append(precision_score(labels, pred_labels))
        self._recording['costs']['recall'].append(recall_score(labels, pred_labels))
        self._recording['costs']['roc_auc'].append(roc_auc_score(labels, pred_scores[:, 1]))
        return accuracy_score(labels, pred_labels),\
               precision_score(labels, pred_labels),\
               recall_score(labels, pred_labels),\
               roc_auc_score(labels, pred_scores[:, 1])

    def _get_pseudo_responses(self, data, labels):
        return [2 * label / float(1 + np.exp(2 * label * self.logit_score(vec))) for vec, label in zip(data, labels)]

    def _init_shapes(self, labels):
        cntr = Counter(labels)
        assert set(cntr.keys()) == {-1, 1}, "Labels must be encoded with -1, 1. Cannot contain more classes."
        assert self._n_dim is not None, "Number of attributes is None"

        self.shapes = {dim: ShapeFunction([np.PINF], [0.5 * np.log10(cntr.get(1, 0)) / cntr.get(-1, 1)])
                       for dim in range(self._n_dim)}

        self.initialized = True

    def train(self, data, labels, n_iter=10, leaning_rate=0.01, display_step=25):
        if not self.initialized:
            self._n_dim = data.shape[1]
            self._init_shapes(labels)

        for epoch in range(n_iter):
            self._recording['epoch'] += 1

            responses = self._get_pseudo_responses(data, labels)
            new_shapes = {dim: self._get_shape_for_attribute(data[:, dim], responses) for dim in range(data.shape[1])}

            for dim, shape in self.shapes.iteritems():
                self.shapes[dim] = shape.add(new_shapes[dim].multiply(leaning_rate))

            acc, prec, rec, auc = self._train_cost(data, labels)
            if (epoch + 1) % display_step == 0:
                print "Epoch:", '{0:04d} / {1:04d}'.format(epoch + 1, n_iter)
                print "accuracy: {}, precision: {}, recall: {}, roc_auc: {}\n".format(acc, prec, rec, auc)

        self.is_fit = True