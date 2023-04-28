tell application "iTerm2"
  tell current window
    set newTab to (create tab with default profile)
    tell current session of newTab
      write text "cd $CEDAR_HOME/cedar-monitoring/cedar-monitoring-src"
      write text "ng serve"
    end tell
  end tell
end tell