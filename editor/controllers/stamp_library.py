"""
NES Open Tournament Golf - Stamp Library

Manages loading and saving of stamp patterns from built-in and user directories.
"""

from pathlib import Path

from editor.data import StampData
from editor.data.category_tree import CategoryTree
from editor.resources import get_resource_path


class StampLibrary:
    """Manages stamp library with built-in and user stamps."""

    def __init__(self):
        """Initialize stamp library."""
        self.stamps: dict[str, StampData] = {}  # stamp_id -> StampData
        self.categories: dict[
            str, list[str]
        ] = {}  # category -> list of stamp_ids (backward compat)
        self.category_tree = CategoryTree()  # Hierarchical category tree
        #: "file: error" for each stamp file the last load_stamps couldn't read
        self.load_errors: list[str] = []

        # Paths
        self.built_in_path = get_resource_path("data/stamps/built-in")
        self.user_path = Path.home() / ".config" / "golf-editor" / "stamps"

        # Ensure user directory exists
        self.user_path.mkdir(parents=True, exist_ok=True)

    def load_stamps(self):
        """Load all stamps from built-in and user directories."""
        self.stamps.clear()
        self.categories.clear()
        self.category_tree.clear()
        self.load_errors = []

        # Load built-in stamps
        if self.built_in_path.exists():
            self._load_stamps_from_directory(self.built_in_path)

        # Load user stamps
        if self.user_path.exists():
            self._load_stamps_from_directory(self.user_path)

    def _load_stamps_from_directory(self, directory: Path):
        """
        Recursively load stamps from a directory.

        Args:
            directory: Directory to scan
        """
        # Scan for JSON files recursively
        for json_file in directory.rglob("*.json"):
            # Skip index.json files
            if json_file.name == "index.json":
                continue

            try:
                stamp = StampData.load(json_file)
                stamp_id = stamp.metadata.id

                # Store stamp
                self.stamps[stamp_id] = stamp

                # Categorize stamp (flat - for backward compatibility)
                category = stamp.metadata.category
                if category not in self.categories:
                    self.categories[category] = []
                self.categories[category].append(stamp_id)

                # Add to category tree (hierarchical)
                self.category_tree.add_stamp(category, stamp_id)

            except Exception as e:
                self.load_errors.append(f"{json_file.name}: {e}")

    def get_stamps_by_path(
        self, category_path: str, recursive: bool = False
    ) -> list[StampData]:
        """
        Get stamps by category path (supports hierarchical paths).

        Args:
            category_path: Category path (e.g., "terrain/water" or just "water")
            recursive: If True, include stamps from subcategories

        Returns:
            List of StampData objects, smallest first
        """
        node = self.category_tree.get_node(category_path)
        if not node:
            return []

        stamp_ids = node.get_all_stamp_ids() if recursive else node.stamp_ids

        stamps = [self.stamps[sid] for sid in stamp_ids if sid in self.stamps]
        return sorted(stamps, key=self._size_order)

    @staticmethod
    def _size_order(stamp: StampData) -> tuple[int, int, int, str]:
        """Sort key: smallest stamp first, by the tiles it places."""
        placed = sum(tile is not None for row in stamp.tiles for tile in row)
        return (placed, stamp.height, stamp.width, stamp.metadata.id)

    def get_stamp(self, stamp_id: str) -> StampData | None:
        """
        Get stamp by ID.

        Args:
            stamp_id: Stamp ID

        Returns:
            StampData or None if not found
        """
        return self.stamps.get(stamp_id)

    def save_stamp(self, stamp: StampData, category: str | None = None) -> Path:
        """
        Save stamp to user directory.

        Args:
            stamp: StampData to save
            category: Optional category subdirectory (uses stamp.metadata.category if None)

        Returns:
            Path where stamp was saved
        """
        # Use stamp's category if not specified
        if category is None:
            category = stamp.metadata.category

        # Create category subdirectory
        category_path = self.user_path / category
        category_path.mkdir(parents=True, exist_ok=True)

        # Generate filename from name or ID
        if stamp.metadata.name:
            # Use name if available (sanitize for filesystem)
            filename = self._sanitize_filename(stamp.metadata.name) + ".json"
        else:
            # Use ID
            filename = f"{stamp.metadata.id}.json"

        save_path = category_path / filename

        # Save stamp
        stamp.save(save_path)

        # Add to library
        self.stamps[stamp.metadata.id] = stamp
        if category not in self.categories:
            self.categories[category] = []
        if stamp.metadata.id not in self.categories[category]:
            self.categories[category].append(stamp.metadata.id)

        return save_path

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """
        Sanitize stamp name for use as filename.

        Args:
            name: Stamp name

        Returns:
            Sanitized filename (without extension)
        """
        # Replace spaces with underscores
        name = name.replace(" ", "_")

        # Remove or replace invalid characters
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, "")

        # Limit length
        if len(name) > 50:
            name = name[:50]

        # Lowercase
        name = name.lower()

        return name or "unnamed_stamp"
