import glob
import os
import pickle
import re
from datetime import datetime
from urllib import unquote
import numpy as np
import tensorflow as tf
from scipy.spatial.distance import jaccard
from datasketch import MinHashLSHForest, MinHash
LSH_FOREST_FILE = 'lsh_forest.pkl'
MAX_AMOUNT_LABELS = 4716
MINIMUM_PROBABILITY = 0.3
forest = None
inferred_labels = dict()

def get_inferred_labels(video_id):
	return inferred_labels.pop(video_id)

def load_forest():
	global forest
	print "[SimpleVideoSearch][{}] Loading pickled LSH Forest".format(datetime.now())
	with open(LSH_FOREST_FILE, 'rb') as forest_file:
		forest = pickle.load(forest_file)
		forest.index()
	print "[SimpleVideoSearch][{}] Done loading pickled LSH Forest".format(datetime.now())

def nearest_neighbor_single_feature(provided, dataset):
	assert len(provided) == len(dataset)
	distance = 0.0
	for index in range(len(provided)):
		distance = distance + abs(provided[index] - dataset[index])
	return distance

def nearest_neighbor_features(provided, dataset):
	#Compare for both the mean_rgb and mean_audio features (the only 2 available)
	provided_mean_rgb = provided.feature["mean_rgb"].float_list.value
	dataset_mean_rgb = dataset.feature["mean_rgb"].float_list.value

	provided_mean_audio = provided.feature["mean_audio"].float_list.value
	dataset_mean_audio = dataset.feature["mean_audio"].float_list.value	

	distance_mean_rgb = nearest_neighbor_single_feature(provided_mean_rgb, dataset_mean_rgb)
	distance_mean_audio = nearest_neighbor_single_feature(provided_mean_audio, dataset_mean_audio)

	return distance_mean_rgb + distance_mean_audio

def convert_inferred_labels_to_tuples_list(inferred_label_probabilities):
	video_id = inferred_label_probabilities['VideoId']
	label_pairs_str = inferred_label_probabilities['LabelConfidencePairs']
	label_pairs_list = label_pairs_str.split()
	label_pairs_length = len(label_pairs_list)
	label_tuples = dict()
	assert label_pairs_length % 2 == 0
	for index in range(0,label_pairs_length, 2):
		label_tuples[int(label_pairs_list[index])] = float(label_pairs_list[index+1])
	return video_id, label_tuples
	
def convert_inferred_labels_to_list(inferred_label_probabilities):
	video_id_str, label_tuples = convert_inferred_labels_to_tuples_list(inferred_label_probabilities)
	stripped_video_id = re.sub(r'[^a-zA-Z0-9_]+', '', video_id_str)
	inferred_labels_full = np.zeros(MAX_AMOUNT_LABELS, dtype=int)
	labels_list = list()
	for label_key, label_probability in label_tuples.iteritems():
		if label_probability >= MINIMUM_PROBABILITY:
			inferred_labels_full[label_key] = 1
			print '[SimpleVideoSearch][{}] Inferred label {} for this video'.format(datetime.now(), label_key)
			labels_list.append(label_key)
	inferred_labels[stripped_video_id] = labels_list
	print '[SimpleVideoSearch][{}] Inferred {} relevant labels'.format(datetime.now(), sum(inferred_labels_full))
	return inferred_labels_full

def convert_dataset_labels_to_list(dataset_labels):
	dataset_labels_full = np.zeros(MAX_AMOUNT_LABELS, dtype=int)
	for label_key in dataset_labels:
		dataset_labels_full[label_key] = 1
	return dataset_labels_full

def similar_videos(provided_features, inferred_label_probabilities):
	train_records = glob.glob("dataset/train00.tfrecord") # FIXME change back to train*.tfrecord
	validate_records = glob.glob("dataset/validate00.tfrecord") # FIXME change back to validate*.tfrecord
	all_records = train_records+validate_records
	dataset = tf.data.TFRecordDataset(all_records)
	iterator = dataset.make_one_shot_iterator()

	count = 0
	# a list of tuples containing the id and nearest-neighbor distance for each element, using the features
	nn_distance = list()
	# a list of tuples containing the id and Jaccard distance for each element, using the inferred label probabilities (not used yet)
	jac_distance = list()
	next_element = iterator.get_next()
	with tf.Session() as sess:
		try:
			inferred_labels_full = convert_inferred_labels_to_list(inferred_label_probabilities)
			while True:
				exampleBinaryString= sess.run(next_element)
				example = tf.train.Example.FromString(exampleBinaryString)
				count += 1
				example_id = example.features.feature["id"].bytes_list.value[0]
				# Compare the provided features with this element of the dataset (nearest neighbor)
				nn_distance.append((example_id, nearest_neighbor_features(provided_features, example.features)))
				
				# Compare the provided inference results with this element of the dataset (Jaccard distance)
				dataset_labels_full = convert_dataset_labels_to_list(example.features.feature["labels"].int64_list.value)
				jac_distance.append((example_id, jaccard(inferred_labels_full, dataset_labels_full)))
		except tf.errors.OutOfRangeError:
			print "[SimpleVideoSearch][{}] Done iterating through dataset".format(datetime.now())
		finally:
			print "[SimpleVideoSearch][{}] Processed {} records from the dataset".format(datetime.now(), count)
	# Sort the lists based on distance
	nn_distance.sort(key = lambda tuple: tuple[1])
	jac_distance.sort(key = lambda tuple: tuple[1])

	# Get the top 10 results
	top10_feature_based = nn_distance[:10]
	top10_label_based = jac_distance[:10]
	return (top10_feature_based, top10_label_based)

def create_LSH_Forest():
	global forest
	if os.path.isfile(LSH_FOREST_FILE):
		load_forest()
	else:
		forest = MinHashLSHForest(num_perm=128)
	train_records = glob.glob("dataset/train*.tfrecord")
	validate_records = glob.glob("dataset/validate*.tfrecord")
	all_records = train_records+validate_records
	dataset = tf.data.TFRecordDataset(all_records)
	iterator = dataset.make_one_shot_iterator()
	count = 0
	next_element = iterator.get_next()
	updated = False
	with tf.Session() as sess:
		try:
			while True:
				if count % 10000 == 0:
					print "[SimpleVideoSearch][{}] Processed {} records from the dataset so far".format(datetime.now(), count)
				if updated and count % 100000 == 0:
					with open(LSH_FOREST_FILE, 'wb') as forest_file:
						forest.index()
						pickle.dump(forest, forest_file, pickle.HIGHEST_PROTOCOL)
					print "[SimpleVideoSearch][{}] Updated LSH Forest file".format(datetime.now(), count)
				exampleBinaryString= sess.run(next_element)
				example = tf.train.Example.FromString(exampleBinaryString)
				count += 1
				example_id = example.features.feature["id"].bytes_list.value[0]
				if example_id not in forest:
					if not updated:
						updated = True
						print '[SimpleVideoSearch][{}] First update at record {}'.format(datetime.now(), count)
					dataset_labels_full = convert_dataset_labels_to_list(example.features.feature["labels"].int64_list.value)
					minhash = MinHash(num_perm=128)
					for label in dataset_labels_full:
						minhash.update(label)
					forest.add(example_id, minhash)
		except tf.errors.OutOfRangeError:
			print "[SimpleVideoSearch][{}] Done iterating through dataset".format(datetime.now())
		finally:
			print "[SimpleVideoSearch][{}] Processed {} records from the dataset".format(datetime.now(), count)
			forest.index()
			with open(LSH_FOREST_FILE, 'wb') as forest_file:
				pickle.dump(forest, forest_file, pickle.HIGHEST_PROTOCOL)
			print "[SimpleVideoSearch][{}] Finished creating LSH Forest file".format(datetime.now(), count)

def similar_videos_from_forest(inferred_label_probabilities):
	inferred_labels_full = convert_inferred_labels_to_list(inferred_label_probabilities)
	minhash = MinHash(num_perm=128)
	for label in inferred_labels_full:
		minhash.update(label)
	
	if forest == None:
		load_forest()
	
	return forest.query(minhash, 10)

if __name__ == '__main__':
	create_LSH_Forest()
