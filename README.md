# `rhythmbox-podqueuer`
A simple [Rhythmbox](https://wiki.gnome.org/Apps/Rhythmbox) plugin that attempts to improve the user experience of listening to podcasts by integrating them with the Play Queue.
* Adds already downloaded unplayed podcasts to Play Queue on startup
* Adds podcasts to the Play Queue when they finish downloading
* Podcasts are always added to Play Queue in release date order
* Remembers playback position for podcasts and restores them on play

## Setup
1. From a terminal in the folder where you wish to hold the code:

   ```Bash
   git clone git@github.com:HebaruSan/rhythmbox-podqueuer.git

   mkdir -p ~/.local/share/rhythmbox/plugins

   ln -s {PATH_TO_HERE}/rhythmbox-podqueuer/plugin ~/.local/share/rhythmbox/plugins/podqueuer
   ```
2. Run Rhythmbox
3. Tools &rarr; Plugins
4. Check the box next to Pod Queuer
5. Close

Your unplayed podcasts will now be listed in the Play Queue, and any newly downloaded podcasts will be added automatically.

## Usage
1. Turn on the left pane, if not already displayed, by pressing F9
2. Show the Play Queue, if not already displayed, by pressing ctrl-K
3. Double click a podcast in the Play Queue

This plugin *only* adds entries to the Play Queue. It does not remove or re-order existing entries. This means you can freely re-order the podcasts or mix in non-podcast songs, but note that this may confuse the date sorting logic of the plugin, as it assumes that the Play Queue will always be sorted in release date order.
