#!/usr/bin/env python

"""
Add podcasts to the Play Queue when they finish downloading.

References:
	https://lazka.github.io/pgi-docs/RB-3.0/index.html
	https://wiki.gnome.org/RhythmboxPlugins/WritingGuide

Filesystem structure borrowed from https://github.com/mzheng/rhythmbox-pandora
(since no one else follows the writing guide's "best practice" w/r/t __init__.py)
"""

from gi.repository import Gio, GLib, GObject, RB, Peas

def podcast_entry_type_name() -> str:
	"""
	This is the name of the entry type that represents podcasts.
	We use it to search for podcasts and the podcast manager.
	Using a function to simulate a const in Python.
	"""

	return 'podcast-post'

def podcast_status_complete() -> int:
	"""
	RHYTHMDB_PODCAST_STATUS_COMPLETE from rhythmdb.h.
	If this is provided somewhere, I can't find it.
	Using a function to simulate a const in Python.
	"""

	return 100

def get_podcast_manager(db: RB.RhythmDB, shell: RB.Shell) -> RB.PodcastManager:
	"""
	Given a RB.RhythmDB object and a RB.Shell object,
	return the RB.PodcastManager object in charge of
	downloading podcasts.
	"""

	podtype = db.entry_type_get_by_name(podcast_entry_type_name())
	podsrc = shell.get_source_by_entry_type(podtype)
	return podsrc.props.podcast_manager

def is_podcast_source_loaded(db: RB.RhythmDB, shell: RB.Shell) -> bool:
	podtype = db.entry_type_get_by_name(podcast_entry_type_name())
	podsrc = shell.get_source_by_entry_type(podtype)
	libsrc = shell.props.library_source
	return podsrc.props.load_status == RB.SourceLoadStatus.LOADED \
		and libsrc.props.populate # set in response to load-complete signal

def is_entry_downloaded(entry: RB.RhythmDBEntry) -> bool:
	"""
	Given a podcast entry, return True if it has been downloaded and False otherwise.
	"""

	return entry.get_ulong(RB.RhythmDBPropType.STATUS) == podcast_status_complete()

def is_entry_unplayed(entry: RB.RhythmDBEntry) -> bool:
	"""
	Given a podcast entry, return True if the play count is 0 and False otherwise.
	"""

	return entry.get_ulong(RB.RhythmDBPropType.PLAY_COUNT) < 1


class PodQueuerPlugin(GObject.Object, Peas.Activatable):
	"""
	Add podcasts to the Play Queue when they finish downloading.

	NOTE: I have only tested this with around 500 podcast
	entries and about 50 that qualify for being added to the
	Play Queue. If you have significantly more podcasts in your
	library, it is possible that this plugin may cause
	performance problems on load or whenever a new podcast is
	auto-added to the Play Queue.

	Attributes:
		object	Rhythmbox will set this reference to its Shell object when it loads us
	"""

	object = GObject.property(type=GObject.Object)

	def __init__(self):
		"""
		The constructor shouldn't do any real work, because we might be instantiated while shut off.
		"""

		super(PodQueuerPlugin, self).__init__()

	def do_activate(self):
		"""
		This function is from the Peas.Activatable interface.
		We use it to set up all of our state, including references and event listeners.
		It also takes care of enqueuing unplayed podcasts that were downloaded previously.
		"""

		# We use this reference to add podcasts to the Play Queue
		self.queue = self.object.props.queue_source

		# Enqueue any already downloaded unplayed podcasts when we start up.
		# If the library is already loaded and we're being activated after the fact,
		# we can do this immediately.
		# Otherwise if we're being activated before the library is loaded,
		# we need to listen for the signal that the library is loaded first.
		if is_podcast_source_loaded(self.object.props.db, self.object):
			self.on_load_complete(self, self.object.props.db)
		else:
			self.load_complete_id = self.object.props.db.connect(
				'load-complete', self.on_load_complete)

		# Get a reference to the PodcastManager so we can listen for completed downloads
		self.podmgr = get_podcast_manager(self.object.props.db, self.object)
		self.finish_download_id = self.podmgr.connect('finish_download', self.on_finish_download)

	def on_load_complete(self, db: RB.RhythmDB):
		"""
		Event handler for RB.RhythmDB.load-complete.
		We use it to enqueue already downloaded unplayed podcasts.
		"""

		self.check_for_unplayed_podcasts(db)

	def check_for_unplayed_podcasts(self, db: RB.RhythmDB):
		"""
		Given a RhythmDB, find any unplayed podcasts and add them to the Play Queue.
		"""

		podtype = db.entry_type_get_by_name(podcast_entry_type_name())
		db.entry_foreach_by_type(podtype, self.on_found_podcast_entry)

	def on_found_podcast_entry(self, entry: RB.RhythmDBEntry):
		"""
		Given a podcast entry, add it to the play queue if its play count is 0.
		"""

		if is_entry_downloaded(entry) and is_entry_unplayed(entry):
			self.found_unplayed_podcast_entry(entry)

	def found_unplayed_podcast_entry(self, entry: RB.RhythmDBEntry):
		"""
		Add an entry to the play queue.
		It is assumed to be unplayed.
		"""

		self.queue.add_entry(entry, self.get_date_insertion_sort_index(self.queue, entry))

	def get_date_insertion_sort_index(self, source: RB.Source, new_entry: RB.RhythmDBEntry) -> int:
		"""
		Figure out where a new Play Queue entry belongs.
		We search the current entries and compare their dates to the new entry's date.
		We return the index of the first entry found that's older than our new entry.
		"""

		model = source.get_entry_view().props.model
		new_date = new_entry.get_ulong(RB.RhythmDBPropType.POST_TIME)
		iter = model.get_iter_first()
		for index in range(model.iter_n_children()):
			entry = model.iter_to_entry(iter)
			date = entry.get_ulong(RB.RhythmDBPropType.POST_TIME)
			if new_date < date:
				return index
			iter = model.iter_next(iter)
		return -1

	def on_finish_download(self, podmgr: RB.PodcastManager, entry: RB.RhythmDBEntry):
		"""
		Event handler for RB.PodcastManager.finish_download event.
		Adds freshly downloaded podcasts to the play queue.
		"""

		self.found_unplayed_podcast_entry(entry)

	def do_deactivate(self):
		"""
		This function is from the Peas.Activatable interface.
		Teardown all state, including references and signals.
		The Play Queue _IS_ preserved on exit/relaunch.
		Since the user may customize it at any time, we don't auto-remove anything.
		"""

		if hasattr(self, 'load_complete_id'):
			self.object.props.db.disconnect(self.load_complete_id)
			del self.load_complete_id
		if hasattr(self, 'finish_download_id'):
			self.podmgr.disconnect(self.finish_download_id)
			del self.finish_download_id
		del self.podmgr
		del self.queue
