tell application "iTerm2"
  tell current window
    tell current session
      write text "cd $CEDAR_HOME"
      write text "stopvaluerecommender"
    end tell
  end tell
end tell