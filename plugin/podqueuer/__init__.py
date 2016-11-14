#!/usr/bin/env python3

"""
Add podcasts to the Play Queue when they finish downloading.
Remember podcast playback positions and restore them when playing.

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
	We use it to search for podcasts, and access the podcast manager,
	and check whether an entry is a podcast.
	Using a function to simulate a const in Python.
	"""

	return 'podcast-post'

def is_entry_a_podcast(entry: RB.RhythmDBEntry) -> bool:
	"""
	Given an entry, return True if it's a podcast and False otherwise.
	"""

	return entry != None and entry.get_entry_type().get_name() == podcast_entry_type_name()

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
	"""
	Return True if the podcast source has been downloaded and False otherwise.
	"""

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

	def __init__(self) -> None:
		"""
		The constructor shouldn't do any real work, because we might be instantiated while shut off.
		"""

		super(PodQueuerPlugin, self).__init__()

	def do_activate(self) -> None:
		"""
		This function is from the Peas.Activatable interface.
		We use it to set up all of our state, including references and event listeners.
		It also takes care of enqueuing unplayed podcasts that were downloaded previously.
		"""

		# We use this reference to add podcasts to the Play Queue
		self.queue = self.object.props.queue_source

		# This reference will hold the currently playing entry, because
		# if we retrieve it on the fly as needed we can get the wrong one.
		self.current_entry = self.object.props.shell_player.get_playing_entry()

		# Enqueue any already-downloaded unplayed podcasts when we start up.
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

		# RB.ExtDB(name=<name>) is stored in ~/.cache/rhythmbox/<name>
		# Don't bother with the 'store' or 'load' signals, as they aren't
		# necessary for simple data (and tend to cause seg faults).
		self.elapsed_store = RB.ExtDB(name='elapsed')

		# Listen for a new podcast playing, so we can jump to the last time position we heard from it
		self.playing_song_changed_id = self.object.props.shell_player.connect('playing-song-changed', self.on_playing_song_changed)

		# Listen for updates as the current track plays, so we can save the last time position
		self.elapsed_changed_id = self.object.props.shell_player.connect('elapsed-changed', self.on_elapsed_changed)

		# Listen for removal from queue
		self.queue_entry_removed_id = self.queue.props.query_model.connect('entry-removed', self.on_queue_entry_removed)

	def elapsed_key(self, entry: RB.RhythmDBEntry, lookup: bool = False) -> RB.ExtDBKey:
		"""
		Generate a key for storing elapsed values in ExtDB.
		You have to use a "store" key to save data and a "lookup" key to load it,
		which will be identical except for the bool 'lookup' flag.

		In our case, we want to store one value per podcast, so we just use the
		unique location property as our key.
		"""

		if lookup:
			return RB.ExtDBKey.create_lookup('location', entry.get_string(RB.RhythmDBPropType.LOCATION))
		else:
			return RB.ExtDBKey.create_storage('location', entry.get_string(RB.RhythmDBPropType.LOCATION))

	def on_playing_song_changed(self, shell_player: RB.ShellPlayer, entry: RB.RhythmDBEntry) -> None:
		"""
		React to a change in the current podcast by jumping to the last place where we played it.
		"""

		self.current_entry = entry
		if not entry == None and is_entry_a_podcast(entry):
			key = self.elapsed_key(entry, True)
			# 'lookup' only returns the storage filename, so we need to do an asynchronous request.
			self.elapsed_store.request(key, self.on_elapsed_store_request, shell_player)

	def on_elapsed_store_request(self, key: RB.ExtDBKey, store_key: RB.ExtDBKey,
			filename: str, data, shell_player: RB.ShellPlayer) -> None:
		"""
		Handle asynchronous loading of the elapsed value.
		"""

		if not data == None:
			elapsed = int(data)
			shell_player.set_playing_time(elapsed)

	def on_elapsed_changed(self, shell_player: RB.ShellPlayer, elapsed: int) -> None:
		"""
		React to a change in the position of the current song by saving it to the elapsed ext db.
		These are sent once per second, which is often enough to be reasonably accurate,
		but not so often as to bog down the system.

		We ignore values less than 3 to avoid clobbering real values with 0 when first resuming a track.
		"""

		if elapsed >= 3 and is_entry_a_podcast(self.current_entry):
			self.set_entry_elapsed(self.current_entry, elapsed)

	def set_entry_elapsed(self, entry: RB.RhythmDBEntry, elapsed: int) -> None:
		"""
		Store 'elapsed' in our ExtDB so we can jump to it later.

		How to store data in ExtDB:

			store_raw is needed to make it actually store without an
			intermediate processing stage.
			See "if (req->data == NULL)" check in do_store_request;
			store() populates req->value, and store_raw() populates req->data.

			RB.ExtDBSourceType.NONE and SEARCH are silently ignored (!),
			and USER_EXPLICIT overrides the rest, so we always use it to
			make sure our store requests aren't ignored.
			See do_store_request in rb-ext-db.c.

			Only string, byte array, and gstring values may be stored.
			See "don't know how to save data of type" error message.
		"""

		key = self.elapsed_key(entry)
		self.elapsed_store.store_raw(key, RB.ExtDBSourceType.USER_EXPLICIT, str(elapsed))

	def on_load_complete(self, db: RB.RhythmDB) -> None:
		"""
		Event handler for RB.RhythmDB.load-complete.
		We use it to enqueue already downloaded unplayed podcasts.
		"""

		self.check_for_unplayed_podcasts(db)

	def check_for_unplayed_podcasts(self, db: RB.RhythmDB) -> None:
		"""
		Given a RhythmDB, find any unplayed podcasts and add them to the Play Queue.
		"""

		podtype = db.entry_type_get_by_name(podcast_entry_type_name())
		db.entry_foreach_by_type(podtype, self.on_found_podcast_entry)

	def on_found_podcast_entry(self, entry: RB.RhythmDBEntry) -> None:
		"""
		Given a podcast entry, add it to the play queue if its play count is 0.
		"""

		if is_entry_downloaded(entry) and is_entry_unplayed(entry):
			self.found_unplayed_podcast_entry(entry)

	def found_unplayed_podcast_entry(self, entry: RB.RhythmDBEntry) -> None:
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

	def on_finish_download(self, podmgr: RB.PodcastManager, entry: RB.RhythmDBEntry) -> None:
		"""
		Event handler for RB.PodcastManager.finish_download event.
		Adds freshly downloaded podcasts to the play queue.
		"""

		self.found_unplayed_podcast_entry(entry)

	def on_queue_entry_removed(self, query_model: RB.RhythmDBQueryModel, entry: RB.RhythmDBEntry) -> None:
		"""
		Tracks are removed from the Play Queue if you jump to another,
		even if they haven't finished playing. Ideally we would intercept
		this action and prevent it, but the best we can do is to re-add it.

		Nothing happens if we try to re-add immediately, so we use idle_add to queue the action for later.
		"""

		GLib.idle_add(self.idle_enqueue_unplayed_podcast, entry)

	def idle_enqueue_unplayed_podcast(self, entry: RB.RhythmDBEntry) -> None:
		"""
		After a track is removed from the Play Queue, re-enqueue it if it's
		an unplayed podcast.

		We do this as a separate idle_add event so the app can finish updating the
		database first.
		"""

		if is_entry_a_podcast(entry) and is_entry_downloaded(entry) and is_entry_unplayed(entry):
			self.found_unplayed_podcast_entry(entry)

	def do_deactivate(self) -> None:
		"""
		This function is from the Peas.Activatable interface.
		Teardown all state, including references and signals.
		The Play Queue _IS_ preserved on exit/relaunch.
		Since the user may customize it at any time, we don't auto-remove anything.
		"""

		if hasattr(self, 'playing_song_changed_id'):
			self.object.props.shell_player.disconnect(self.playing_song_changed_id)
			del self.playing_song_changed_id

		if hasattr(self, 'elapsed_changed_id'):
			self.object.props.shell_player.disconnect(self.elapsed_changed_id)
			del self.elapsed_changed_id

		if hasattr(self, 'load_complete_id'):
			self.object.props.db.disconnect(self.load_complete_id)
			del self.load_complete_id

		if hasattr(self, 'finish_download_id'):
			self.podmgr.disconnect(self.finish_download_id)
			del self.finish_download_id

		if hasattr(self, 'queue_entry_removed_id'):
			self.queue.props.query_model.disconnect(self.queue_entry_removed_id)
			del self.queue_entry_removed_id

		del self.podmgr
		del self.queue
		del self.elapsed_store
		del self.current_entry
